"""JobRunner — per-user lock + global concurrency cap + subscriber fanout.

Why this exists: the QTO pipeline (``Assembler.process_page`` +
``MultiAgentClient`` + ``HistoricalStore``) is **NOT** thread-safe.
Two concurrent extractions for the same user would race on the
``HistoricalStore`` SQLite connection and the AIClient compose-cache;
two concurrent extractions for *different* users on the same uvicorn
worker hit the same global SQLite lock. Both must serialise.

Strategy:

* **Global semaphore** — caps total concurrent extractions across all
  users to ``Settings.max_concurrent_jobs`` (default 3). This is the
  pure-CPU + AI-budget guard.
* **Per-user lock** — guarantees a single user can only have one
  extraction running at a time. Eliminates the per-user cache races.
* **Subscriber fanout** — each running extraction publishes to a
  primary :class:`asyncio.Queue` owned by the JobRunner. The SSE
  endpoint registers a *consumer queue* per request; the runner's
  publish loop forwards every event into every consumer. Reconnects
  miss events that fired during the disconnect; the route layer can
  hydrate from Postgres on subscription if needed.

Lifecycle (one job):

    POST /api/extractions →
        JobRunner.start_job(extraction_id, ...)
            → spawn asyncio.Task that awaits the semaphore + lock,
              instantiates ExtractionRunner, runs it, then forwards
              the runner's queue events to all subscribers.

    GET /api/extractions/{id}/events →
        JobRunner.subscribe(extraction_id) → AsyncIterator[event]

    POST /api/extractions/{id}/cancel →
        JobRunner.cancel(extraction_id) → sets the runner's
        ``threading.Event``; the worker yields at the next page boundary.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import UUID

from backend.config import get_settings


logger = logging.getLogger(__name__)


# ── State containers ────────────────────────────────────────────────


@dataclass
class _JobState:
    """Per-extraction runtime state owned by the JobRunner."""

    extraction_id: UUID
    user_id: UUID
    cancel_event: threading.Event = field(default_factory=threading.Event)
    runner_task: asyncio.Task | None = None
    primary_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    finished: asyncio.Event = field(default_factory=asyncio.Event)


# ── JobRunner ───────────────────────────────────────────────────────


class JobRunner:
    """Process-singleton coordinator for extraction jobs.

    Construct once at app startup (FastAPI lifespan) and reuse via
    :func:`get_job_runner` from routes. The internal state dies when
    the process exits — multi-worker uvicorn would need Redis pub/sub
    to share, which is documented as a v2 scaling task in the plan.
    """

    def __init__(self, max_concurrent: int | None = None):
        settings = get_settings()
        cap = max_concurrent or settings.max_concurrent_jobs
        self._global_semaphore = asyncio.Semaphore(cap)
        self._user_locks: dict[UUID, asyncio.Lock] = {}
        self._jobs: dict[UUID, _JobState] = {}
        self._lock = asyncio.Lock()  # guards mutations of _jobs / _user_locks

    # ── Public API ──────────────────────────────────────────────

    async def start_job(
        self,
        *,
        extraction_id: UUID,
        user_id: UUID,
        pdf_id: UUID,
        local_pdf_path: Path,
        config: dict,
    ) -> _JobState:
        """Spawn the extraction task. Returns immediately.

        The task itself awaits the semaphore + per-user lock before
        starting any work, so multiple POSTs for the same user queue
        cleanly without overlapping.
        """
        # Late import — avoids a circular hit between services/jobs.py
        # and services/extraction_runner.py.
        from backend.services.extraction_runner import ExtractionRunner

        async with self._lock:
            if extraction_id in self._jobs:
                return self._jobs[extraction_id]

            state = _JobState(extraction_id=extraction_id, user_id=user_id)
            self._jobs[extraction_id] = state
            user_lock = self._user_locks.setdefault(user_id, asyncio.Lock())

        loop = asyncio.get_running_loop()
        runner = ExtractionRunner(
            extraction_id=extraction_id,
            user_id=user_id,
            pdf_id=pdf_id,
            local_pdf_path=local_pdf_path,
            config=config,
            event_loop=loop,
            event_queue=state.primary_queue,
            cancel_event=state.cancel_event,
        )

        async def _coordinator() -> None:
            try:
                async with self._global_semaphore, user_lock:
                    await runner.run()
            except Exception:
                logger.exception(
                    "job %s coordinator failed", extraction_id
                )
            finally:
                # Drain the primary queue out to subscribers and signal
                # finish. Any events still buffered are forwarded; new
                # subscribers after this point still get the snapshot
                # via the route layer's DB read.
                state.finished.set()

        # Fanout task: copies every event from primary queue → subscribers.
        async def _fanout() -> None:
            while True:
                event = await state.primary_queue.get()
                # Push to every subscriber. Slow consumers don't block
                # the others — we use put_nowait + drop on full queues
                # (subscribers should drain fast for SSE).
                for sub in list(state.subscribers):
                    try:
                        sub.put_nowait(event)
                    except asyncio.QueueFull:
                        logger.warning(
                            "subscriber queue full; dropping event for %s",
                            extraction_id,
                        )
                if event.get("type") in ("done", "error", "canceled"):
                    return

        # Compose: kick off both. The coordinator owns the runner; the
        # fanout drains the runner's queue. If the coordinator throws,
        # the fanout still runs to drain whatever's in flight + emits
        # the terminator the runner pushed.
        async def _job() -> None:
            fanout_task = asyncio.create_task(_fanout())
            try:
                await _coordinator()
            finally:
                # Give fanout a final chance to drain before we tear it
                # down. ``ExtractionRunner`` always emits a terminator
                # event in its except / finally paths so fanout returns
                # naturally; we add a timeout as a belt-and-suspenders
                # guard.
                try:
                    await asyncio.wait_for(fanout_task, timeout=5.0)
                except asyncio.TimeoutError:
                    fanout_task.cancel()

        state.runner_task = asyncio.create_task(_job())
        logger.info(
            "job: started extraction=%s user=%s pdf=%s",
            extraction_id, user_id, pdf_id,
        )
        return state

    async def cancel(self, extraction_id: UUID) -> bool:
        """Signal the runner to stop at the next page boundary.

        Returns ``True`` if a job was found and signaled. The DB
        ``status`` flips to ``canceled`` only when the worker thread
        reaches its next ``cancel_event.is_set()`` check — typical
        latency is a few seconds (one page).
        """
        state = self._jobs.get(extraction_id)
        if state is None:
            return False
        state.cancel_event.set()
        logger.info("job: cancel requested for %s", extraction_id)
        return True

    async def subscribe(
        self, extraction_id: UUID,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async iterator yielding events for one extraction.

        Adds a fresh consumer queue to the job's subscriber list,
        yields events as they arrive, removes the queue on iterator
        exit (caller disconnect, or terminator event).
        """
        state = self._jobs.get(extraction_id)
        if state is None:
            # No active job — nothing to stream. Caller should fall
            # back to the snapshot endpoint.
            return

        consumer: asyncio.Queue = asyncio.Queue(maxsize=200)
        state.subscribers.append(consumer)
        try:
            while True:
                event = await consumer.get()
                yield event
                if event.get("type") in ("done", "error", "canceled"):
                    return
        finally:
            try:
                state.subscribers.remove(consumer)
            except ValueError:
                pass

    async def cleanup_finished(self, *, max_age_s: float = 600.0) -> None:
        """Drop _JobState entries for jobs that finished long ago.

        Hook this off a background task if memory becomes an issue;
        for v1 the dict grows by one entry per extraction and each
        entry is small (locks + a settled queue + dataclass).
        """
        del max_age_s  # placeholder — finished_at isn't tracked yet
        async with self._lock:
            for ext_id, state in list(self._jobs.items()):
                if state.finished.is_set():
                    if not state.subscribers:
                        del self._jobs[ext_id]


# ── Singleton accessor ──────────────────────────────────────────────


_runner_singleton: JobRunner | None = None


def get_job_runner() -> JobRunner:
    """Return the process-wide ``JobRunner`` (lazy-init).

    Used as a FastAPI dependency from extraction routes.
    """
    global _runner_singleton
    if _runner_singleton is None:
        _runner_singleton = JobRunner()
    return _runner_singleton


def reset_job_runner() -> None:
    """Tear down the singleton — for tests."""
    global _runner_singleton
    _runner_singleton = None


__all__ = ["JobRunner", "get_job_runner", "reset_job_runner"]
