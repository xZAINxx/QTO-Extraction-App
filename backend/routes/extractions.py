"""Extraction job routes — start, status, cancel, live-events SSE.

The route layer is thin — heavy lifting (concurrency control, the
worker thread, queue fanout) lives in ``services/jobs.py``. Routes
just resolve auth + ownership, hand the job to the runner, and
serialise responses.

The SSE endpoint (`/events`) opens a persistent stream while the
extraction is alive. Reconnects after disconnect get a fresh stream;
the route layer reads the current `Extraction` row + recent
`token_events` from Postgres so the client always knows where things
stand even if it missed events mid-flight.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import Extraction, Pdf, Project, TokenEvent, User, get_db
from backend.middleware.auth import current_user
from backend.services.jobs import JobRunner, get_job_runner
from backend.services.sse import stream_with_heartbeat
from backend.services.storage import Storage, StorageError, get_storage


logger = logging.getLogger(__name__)

router = APIRouter(tags=["extractions"])


# ── Schemas ─────────────────────────────────────────────────────────


class ExtractionStart(BaseModel):
    pdf_id: UUID
    extraction_mode: str | None = None  # override the user's preference


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pdf_id: UUID
    user_id: UUID
    status: str
    extraction_mode: str
    cost_saver_mode: bool
    cost_usd: float
    total_tokens: int
    api_calls: int
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class CostSummary(BaseModel):
    total_cost_usd: float
    total_tokens: int
    total_api_calls: int
    by_model: dict[str, dict[str, float]]


# ── Helpers ─────────────────────────────────────────────────────────


def _load_repo_config() -> dict:
    """Read ``config.yaml`` at the repo root — same source the desktop
    app uses. Per-user overrides (extraction_mode preference) layer on
    top in the start route below.
    """
    repo_root = Path(__file__).resolve().parents[2]
    cfg_path = repo_root / "config.yaml"
    if not cfg_path.is_file():
        return {}
    with cfg_path.open() as fp:
        return yaml.safe_load(fp) or {}


async def _load_owned_extraction(
    extraction_id: UUID, user: User, db: AsyncSession,
) -> Extraction:
    result = await db.execute(
        select(Extraction).where(
            Extraction.id == extraction_id,
            Extraction.user_id == user.id,
        )
    )
    extraction = result.scalar_one_or_none()
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="extraction not found",
        )
    return extraction


# ── Routes ──────────────────────────────────────────────────────────


@router.post(
    "/api/extractions",
    response_model=ExtractionOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_extraction(
    payload: ExtractionStart,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> ExtractionOut:
    """Kick off an extraction job for a user-owned PDF.

    Ownership is verified through ``Pdf → Project → user_id``.
    The PDF is materialised to a local file (temp dir for Supabase,
    in-place for LocalDisk) before the worker thread starts —
    ``ExtractionRunner`` expects a real filesystem path.
    """
    # Verify PDF ownership through the project chain.
    pdf_q = await db.execute(
        select(Pdf, Project)
        .join(Project, Project.id == Pdf.project_id)
        .where(Pdf.id == payload.pdf_id, Project.user_id == user.id)
    )
    row = pdf_q.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pdf not found",
        )
    pdf, _project = row

    # Resolve extraction mode: explicit payload > user preference.
    extraction_mode = (
        payload.extraction_mode or user.extraction_mode or "hybrid"
    )
    if extraction_mode not in ("hybrid", "multi_agent", "claude_only"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown extraction_mode: {extraction_mode!r}",
        )

    # Materialise PDF to a local path the worker can fitz.open.
    # For LocalDiskStorage this is a no-op (yields the live file). For
    # SupabaseStorage it downloads to a tmpfile we then keep around for
    # the worker; cleanup happens via ``cleanup_temp_file`` after the
    # worker finishes (registered as a finalisation step on the runner
    # task — see services/jobs.py).
    try:
        # We don't use the context manager directly because the worker
        # outlives this route. Instead we materialise once and pass the
        # path through. SupabaseStorage's ``local_path`` cleans up via
        # its context manager — for the worker case we manually mirror
        # that by copying bytes to a stable tmp path.
        local_path = _materialise_pdf(storage, pdf.storage_key)
    except StorageError as exc:
        logger.exception("materialise failed for %s", pdf.storage_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="storage unavailable",
        ) from exc

    # Snapshot config.yaml + per-user overrides into one merged dict for
    # the worker. Reads from disk every time so a config edit in dev
    # propagates without restarting uvicorn.
    repo_cfg = _load_repo_config()
    settings = get_settings()
    repo_cfg["extraction_mode"] = extraction_mode
    repo_cfg["cost_saver_mode"] = bool(user.cost_saver_mode)
    # Inject env-driven keys so the AI clients pick them up.
    repo_cfg.setdefault(
        "anthropic_api_key", os.environ.get("ANTHROPIC_API_KEY", "")
    )
    repo_cfg.setdefault("cache_dir", str(settings.storage_local_root.parent / "cache"))

    # Create the Extraction row before launching the worker so the
    # SSE subscribe path has something to read.
    extraction = Extraction(
        pdf_id=pdf.id,
        user_id=user.id,
        status="pending",
        extraction_mode=extraction_mode,
        cost_saver_mode=bool(user.cost_saver_mode),
    )
    db.add(extraction)
    await db.commit()
    await db.refresh(extraction)

    await runner.start_job(
        extraction_id=extraction.id,
        user_id=user.id,
        pdf_id=pdf.id,
        local_pdf_path=local_path,
        config=repo_cfg,
    )

    logger.info(
        "extraction: started id=%s mode=%s pdf=%s user=%s",
        extraction.id, extraction_mode, pdf.id, user.id,
    )
    return ExtractionOut.model_validate(extraction)


@router.get(
    "/api/extractions/{extraction_id}",
    response_model=ExtractionOut,
)
async def get_extraction(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExtractionOut:
    extraction = await _load_owned_extraction(extraction_id, user, db)
    return ExtractionOut.model_validate(extraction)


@router.post(
    "/api/extractions/{extraction_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_extraction(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> dict:
    """Signal the worker to stop at the next page boundary."""
    await _load_owned_extraction(extraction_id, user, db)
    accepted = await runner.cancel(extraction_id)
    return {"accepted": accepted, "extraction_id": str(extraction_id)}


@router.get("/api/extractions/{extraction_id}/events")
async def stream_extraction_events(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
) -> StreamingResponse:
    """SSE stream of progress / row_ready / tokens / done events.

    On reconnect (no active job in memory), we still emit a single
    ``snapshot`` event derived from the DB so the client sees the
    current status without polling.
    """
    extraction = await _load_owned_extraction(extraction_id, user, db)

    async def _event_iterator():
        # Always send a snapshot first so the client renders something
        # even if the job already finished.
        snapshot = {
            "type": "snapshot",
            "status": extraction.status,
            "cost_usd": float(extraction.cost_usd or 0),
            "total_tokens": int(extraction.total_tokens or 0),
            "api_calls": int(extraction.api_calls or 0),
            "started_at": (
                extraction.started_at.isoformat()
                if extraction.started_at else None
            ),
            "finished_at": (
                extraction.finished_at.isoformat()
                if extraction.finished_at else None
            ),
        }
        yield f"data: {_json(snapshot)}\n\n"

        # If the job already finished, terminate immediately.
        if extraction.status in ("completed", "failed", "canceled"):
            yield "data: [DONE]\n\n"
            return

        # Otherwise tail the live event queue with heartbeat.
        async for chunk in stream_with_heartbeat(
            _build_subscriber_queue(runner, extraction_id),
        ):
            yield chunk

    return StreamingResponse(
        _event_iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.get(
    "/api/extractions/{extraction_id}/cost",
    response_model=CostSummary,
)
async def get_cost_summary(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CostSummary:
    """Aggregate token + cost usage by model — drives the cost popover."""
    await _load_owned_extraction(extraction_id, user, db)

    by_model_q = await db.execute(
        select(
            TokenEvent.model,
            func.sum(TokenEvent.api_calls).label("api_calls"),
            func.sum(TokenEvent.input_tokens + TokenEvent.output_tokens)
                .label("tokens"),
            func.sum(TokenEvent.cost_usd).label("cost_usd"),
        )
        .where(TokenEvent.extraction_id == extraction_id)
        .group_by(TokenEvent.model)
        .order_by(desc("cost_usd"))
    )

    by_model: dict[str, dict[str, float]] = {}
    total_cost = 0.0
    total_tokens = 0
    total_calls = 0
    for model, calls, tokens, cost in by_model_q.all():
        calls_i = int(calls or 0)
        tokens_i = int(tokens or 0)
        cost_f = float(cost or 0)
        by_model[model] = {
            "api_calls": calls_i,
            "tokens": tokens_i,
            "cost_usd": cost_f,
        }
        total_calls += calls_i
        total_tokens += tokens_i
        total_cost += cost_f

    return CostSummary(
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        total_api_calls=total_calls,
        by_model=by_model,
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _materialise_pdf(storage: Storage, key: str) -> Path:
    """Copy the PDF to a stable tmp file the worker can hold open.

    LocalDiskStorage already returns a real path; this just yields it.
    SupabaseStorage downloads bytes to a tmpfile we keep around for the
    duration of the extraction. Cleanup is best-effort — we don't track
    these tempfiles right now (the OS reaps ``/tmp`` regularly), and
    the per-job storage footprint is tiny.
    """
    # ``local_path`` is a context manager but we need the path to outlive
    # the function. We open it, copy to a stable tmp, close the CM, and
    # return the stable path. For LocalDiskStorage the copy is a no-op
    # (we'd just return the path), but we keep the symmetric behaviour
    # so the worker's cleanup logic doesn't accidentally delete the
    # canonical storage file.
    with storage.local_path(key) as src:
        # Materialise to a stable tmp file the worker keeps reading.
        tmp = tempfile.NamedTemporaryFile(
            prefix="qto-extract-",
            suffix=".pdf",
            delete=False,
        )
        try:
            with open(src, "rb") as fp:
                while True:
                    chunk = fp.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
        finally:
            tmp.close()
        return Path(tmp.name)


def _build_subscriber_queue(runner: JobRunner, extraction_id: UUID):
    """Return an asyncio.Queue subscribed to the runner's fanout.

    We unwrap the ``subscribe`` async-generator into a plain queue so
    ``stream_with_heartbeat`` can treat it uniformly. If the job has
    already finished, ``subscribe`` yields nothing — the snapshot the
    SSE handler already emitted is the entire response.
    """
    import asyncio as _asyncio

    queue: _asyncio.Queue = _asyncio.Queue(maxsize=200)

    async def _pump() -> None:
        async for event in runner.subscribe(extraction_id):
            await queue.put(event)
        # Force a terminator if the runner tore down without emitting.
        await queue.put({"type": "done", "from_cache": False})

    _asyncio.create_task(_pump())
    return queue


def _json(payload: dict) -> str:
    """Inline JSON encoder — small and dependency-free."""
    import json
    return json.dumps(payload, default=str, separators=(",", ":"))


__all__ = ["router"]
