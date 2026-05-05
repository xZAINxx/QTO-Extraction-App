"""Synchronous extraction worker — the asyncio-friendly port of
``ui/controllers/extraction_worker.py``.

The QTO pipeline (``core.assembler.Assembler``, ``ai.client.AIClient``,
``ai.multi_agent_client.MultiAgentClient``) is fully synchronous and
blocks for seconds-to-minutes on AI calls. Running it directly on the
FastAPI event loop would freeze every request. Instead we wrap one
extraction in a single :func:`asyncio.to_thread` call and let
``ExtractionRunner._run_blocking`` execute on the threadpool.

Inside the thread we:

* persist progress + rows + token events to Postgres via a sync
  ``Session`` (the dedicated sync pool from ``backend.db.session``);
* publish lifecycle events back to an :class:`asyncio.Queue` on the
  event loop via ``loop.call_soon_threadsafe`` — the SSE handler in
  ``backend.routes.extractions`` drains it and forwards to the client.

State machine for ``Extraction.status``:

    pending → running → (completed | failed | canceled)

Cancellation is cooperative: the runner checks
``self._cancel_event.is_set()`` before each page; the event is set
from the cancel route via :class:`backend.services.jobs.JobRunner`.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

# Ensure the QTO domain modules (``ai/``, ``core/``, ``parser/``) are
# importable. ``backend/main.py`` already does this on app startup, but
# the worker can be exercised in isolation via tests so we add the path
# defensively.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.db import Extraction, Pdf, QtoRow, TokenEvent, get_sync_session_factory


logger = logging.getLogger(__name__)


# ── Event helpers ───────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Runner ──────────────────────────────────────────────────────────


class ExtractionRunner:
    """Run the QTO pipeline against one PDF.

    Construction is cheap (no DB / fitz / AI work). Call :meth:`run`
    from an async context — it dispatches to the threadpool and awaits
    the synchronous body.
    """

    def __init__(
        self,
        *,
        extraction_id: UUID,
        user_id: UUID,
        pdf_id: UUID,
        local_pdf_path: Path,
        config: dict,
        event_loop: asyncio.AbstractEventLoop,
        event_queue: asyncio.Queue,
        cancel_event: threading.Event,
        sync_session_factory: sessionmaker[Session] | None = None,
    ):
        self.extraction_id = extraction_id
        self.user_id = user_id
        self.pdf_id = pdf_id
        self.local_pdf_path = Path(local_pdf_path)
        self.config = config
        self._loop = event_loop
        self._queue = event_queue
        self._cancel_event = cancel_event
        self._session_factory = sync_session_factory or get_sync_session_factory()

    # ── Public API ──────────────────────────────────────────────

    async def run(self) -> None:
        """Awaitable wrapper — pushes execution to the threadpool."""
        await asyncio.to_thread(self._run_blocking)

    # ── Internal: thread-safe queue emission ────────────────────

    def _emit(self, event: dict[str, Any]) -> None:
        """Schedule ``queue.put_nowait(event)`` on the event loop.

        The runner is on a worker thread; ``asyncio.Queue`` operations
        are NOT thread-safe. ``call_soon_threadsafe`` bounces the call
        back onto the event loop where it's safe.
        """
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            # Loop closed — extraction was canceled and the route
            # handler tore down the queue. Drop silently.
            pass

    # ── Sync body (runs on a thread) ────────────────────────────

    def _run_blocking(self) -> None:
        """Synchronous pipeline. Runs to completion or until canceled."""
        from core.assembler import Assembler
        from core.cache import ResultCache
        from core.token_tracker import TokenTracker
        from core.validator import validate
        from parser.pdf_splitter import split_and_classify

        try:
            self._mark_running()

            self._emit({
                "type": "progress",
                "phase": "starting",
                "page": 0,
                "total": 0,
                "page_type": None,
                "ts": _now_iso(),
            })

            cache = ResultCache(self.config.get("cache_dir", "./cache"))
            tracker = TokenTracker()
            tracker.on_update(self._on_tokens_updated)

            mode = self.config.get("extraction_mode", "hybrid")
            if mode == "multi_agent":
                from ai.multi_agent_client import MultiAgentClient
                ai_client = MultiAgentClient(self.config, tracker)
            else:
                from ai.client import AIClient
                ai_client = AIClient(self.config, tracker)
            assembler = Assembler(self.config, ai_client, tracker)

            pdf_path_str = str(self.local_pdf_path)

            # Cache hit short-circuit: identical to the desktop worker.
            cached = cache.load(pdf_path_str)
            if cached is not None:
                logger.info(
                    "extraction %s: cache hit (%d rows)",
                    self.extraction_id, len(cached),
                )
                self._persist_rows(cached)
                self._mark_finished(cost_usd=0.0, total_tokens=0, api_calls=0)
                self._emit({
                    "type": "done",
                    "from_cache": True,
                    "row_count": len(cached),
                    "cost_usd": 0.0,
                    "ts": _now_iso(),
                })
                return

            import fitz  # type: ignore[import-untyped]

            with fitz.open(pdf_path_str) as doc:
                total_pages = doc.page_count

            all_rows: list = []
            position_offset = 0
            classifications: dict = {}

            for page, page_info in split_and_classify(pdf_path_str):
                if self._cancel_event.is_set():
                    logger.info(
                        "extraction %s: canceled at page %s",
                        self.extraction_id, page_info.page_num,
                    )
                    self._mark_canceled()
                    self._emit({
                        "type": "canceled",
                        "page": page_info.page_num,
                        "ts": _now_iso(),
                    })
                    return

                self._emit({
                    "type": "progress",
                    "phase": "processing",
                    "page": page_info.page_num,
                    "total": total_pages,
                    "page_type": page_info.page_type,
                    "ts": _now_iso(),
                })

                classifications[str(page_info.page_num)] = {
                    "page_type": page_info.page_type,
                    "skip": page_info.skip,
                    "skip_reason": page_info.skip_reason,
                    "text": (page_info.text or "")[:200],
                }

                rows = assembler.process_page(page, page_info, pdf_path_str)
                if rows:
                    self._persist_rows(rows, start_position=position_offset)
                    position_offset += len(rows)
                    all_rows.extend(rows)
                    self._emit({
                        "type": "row_ready",
                        "page": page_info.page_num,
                        "rows": [_row_to_dict(r) for r in rows],
                        "ts": _now_iso(),
                    })

            if self._cancel_event.is_set():
                self._mark_canceled()
                self._emit({"type": "canceled", "ts": _now_iso()})
                return

            grouped = assembler.sort_by_sheet(all_rows)
            try:
                validate(grouped)
            except Exception as exc:  # noqa: BLE001 — validator throws AssertionError
                logger.warning(
                    "extraction %s: validation failed: %s",
                    self.extraction_id, exc,
                )

            cache.save(pdf_path_str, grouped, classifications)

            usage = tracker.snapshot() if hasattr(tracker, "snapshot") else None
            cost_usd = float(getattr(usage, "estimated_cost_usd", 0.0) or 0.0)
            total_tokens = int(
                getattr(usage, "input_tokens", 0)
                + getattr(usage, "output_tokens", 0),
            )
            api_calls = int(getattr(usage, "api_calls", 0) or 0)

            self._mark_finished(
                cost_usd=cost_usd,
                total_tokens=total_tokens,
                api_calls=api_calls,
            )
            self._emit({
                "type": "done",
                "from_cache": False,
                "row_count": len(all_rows),
                "cost_usd": cost_usd,
                "total_tokens": total_tokens,
                "api_calls": api_calls,
                "ts": _now_iso(),
            })

        except Exception as exc:
            logger.exception("extraction %s: failure", self.extraction_id)
            self._mark_failed(str(exc))
            self._emit({
                "type": "error",
                "message": str(exc),
                "traceback": traceback.format_exc()[-2000:],
                "ts": _now_iso(),
            })

    # ── Persistence helpers (sync DB) ────────────────────────────

    def _mark_running(self) -> None:
        with self._session_factory() as session:
            ext = session.get(Extraction, self.extraction_id)
            if ext is None:
                return
            ext.status = "running"
            ext.started_at = datetime.now(timezone.utc)
            session.commit()

    def _mark_canceled(self) -> None:
        with self._session_factory() as session:
            ext = session.get(Extraction, self.extraction_id)
            if ext is None:
                return
            ext.status = "canceled"
            ext.finished_at = datetime.now(timezone.utc)
            session.commit()

    def _mark_failed(self, message: str) -> None:
        with self._session_factory() as session:
            ext = session.get(Extraction, self.extraction_id)
            if ext is None:
                return
            ext.status = "failed"
            ext.finished_at = datetime.now(timezone.utc)
            ext.error_message = message[:2000]
            session.commit()

    def _mark_finished(
        self, *, cost_usd: float, total_tokens: int, api_calls: int,
    ) -> None:
        with self._session_factory() as session:
            ext = session.get(Extraction, self.extraction_id)
            if ext is None:
                return
            ext.status = "completed"
            ext.finished_at = datetime.now(timezone.utc)
            ext.cost_usd = cost_usd
            ext.total_tokens = total_tokens
            ext.api_calls = api_calls
            session.commit()

    def _persist_rows(self, rows: list, *, start_position: int = 0) -> None:
        if not rows:
            return
        with self._session_factory() as session:
            for i, row in enumerate(rows):
                row_dict = _row_to_dict(row)
                session.add(QtoRow(
                    id=uuid4(),
                    extraction_id=self.extraction_id,
                    position=start_position + i,
                    s_no=row_dict.get("s_no"),
                    tag=row_dict.get("tag") or None,
                    drawings=row_dict.get("drawings") or None,
                    details=row_dict.get("details") or None,
                    description=row_dict.get("description") or None,
                    qty=row_dict.get("qty"),
                    units=row_dict.get("units") or None,
                    unit_price=row_dict.get("unit_price"),
                    total_formula=row_dict.get("total_formula") or None,
                    math_trail=row_dict.get("math_trail") or None,
                    trade_division=row_dict.get("trade_division") or None,
                    source_page=row_dict.get("source_page"),
                    source_sheet=row_dict.get("source_sheet") or None,
                    extraction_method=row_dict.get("extraction_method") or None,
                    confidence=row_dict.get("confidence"),
                    bbox=_bbox_to_jsonb(row_dict.get("bbox")),
                    is_header_row=bool(row_dict.get("is_header_row", False)),
                    confirmed=bool(row_dict.get("confirmed", False)),
                    needs_review=bool(row_dict.get("needs_review", False)),
                    risk_flags=list(row_dict.get("risk_flags") or []),
                ))
            session.commit()

    # ── TokenTracker callback (fires on the worker thread) ──────

    def _on_tokens_updated(self, usage: Any) -> None:
        """Persist a TokenEvent row and emit a tokens-event SSE.

        Runs on the worker thread (TokenTracker fires synchronously).
        DB write is fine here — sync session.
        """
        try:
            with self._session_factory() as session:
                # ``by_model`` is a dict of model -> ModelUsage. Persist
                # one row per model per call so the cost popover can
                # break down by family.
                by_model = getattr(usage, "by_model", None) or {}
                if by_model:
                    for model, mu in by_model.items():
                        session.add(TokenEvent(
                            id=uuid4(),
                            extraction_id=self.extraction_id,
                            model=model,
                            api_calls=int(getattr(mu, "api_calls", 0) or 0),
                            input_tokens=int(getattr(mu, "input_tokens", 0) or 0),
                            output_tokens=int(getattr(mu, "output_tokens", 0) or 0),
                            cache_read_tokens=int(
                                getattr(mu, "cache_read_tokens", 0) or 0
                            ),
                            cache_write_tokens=int(
                                getattr(mu, "cache_write_tokens", 0) or 0
                            ),
                            cost_usd=float(
                                mu.cost_usd(model)
                                if hasattr(mu, "cost_usd")
                                else 0.0
                            ),
                        ))
                else:
                    session.add(TokenEvent(
                        id=uuid4(),
                        extraction_id=self.extraction_id,
                        model="unknown",
                        api_calls=int(getattr(usage, "api_calls", 0) or 0),
                        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                        cache_read_tokens=int(
                            getattr(usage, "cache_read_tokens", 0) or 0
                        ),
                        cache_write_tokens=int(
                            getattr(usage, "cache_write_tokens", 0) or 0
                        ),
                        cost_usd=float(
                            getattr(usage, "estimated_cost_usd", 0.0) or 0.0
                        ),
                    ))
                session.commit()
        except Exception:
            # Never let a logging-side write blow up the extraction.
            logger.exception("token-event persist failed")

        self._emit({
            "type": "tokens",
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(usage, "cache_write_tokens", 0) or 0),
            "api_calls": int(getattr(usage, "api_calls", 0) or 0),
            "cost_usd": float(getattr(usage, "estimated_cost_usd", 0.0) or 0.0),
            "by_model": {
                m: {
                    "api_calls": int(getattr(mu, "api_calls", 0) or 0),
                    "cost_usd": float(
                        mu.cost_usd(m) if hasattr(mu, "cost_usd") else 0.0
                    ),
                }
                for m, mu in (getattr(usage, "by_model", None) or {}).items()
            },
            "ts": _now_iso(),
        })


# ── Row serialisation ───────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Coerce a ``QTORow`` (dataclass) to a plain dict suitable for JSON.

    Tolerates the row already being a dict (for cached payloads that
    serialised + reloaded as dicts via ``ResultCache``).
    """
    if isinstance(row, dict):
        return dict(row)
    if is_dataclass(row):
        return asdict(row)
    # Last-resort: use ``__dict__``. PyQt-side rows may have private
    # attributes prefixed with ``_`` we don't want to forward.
    return {
        k: v for k, v in vars(row).items() if not k.startswith("_")
    }


def _bbox_to_jsonb(bbox: Any) -> Any:
    """Normalise the bbox 4-tuple to a JSON-serialisable list."""
    if bbox is None:
        return None
    try:
        return [float(x) for x in bbox]
    except (TypeError, ValueError):
        return None


__all__ = ["ExtractionRunner"]
