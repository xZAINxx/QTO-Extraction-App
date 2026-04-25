"""Phase-7: Anthropic Message Batches API runner.

The Batches API gives a 50% discount on token cost in exchange for
async delivery (anywhere from a few minutes to 24 hours). It's a perfect
fit for our two heaviest workloads:

* description composition — happens once per row, no UI dependency
* CSI / page-type / scope classifications — same shape

Design:

* :class:`BatchRequest` is a lightweight DTO carrying the full
  ``messages.create`` argument set plus a stable ``custom_id`` used to
  match results back to call sites.
* :class:`BatchRunner.run` submits one batch, polls with exponential
  backoff (capped at 30 s between checks), and returns
  ``{custom_id: text}``. Calls a ``progress`` callback every poll with
  the current ``processing / succeeded / errored`` counts.
* The runner reports back via callback rather than blocking the UI
  thread — :class:`ui.main_window.MainWindow` runs it inside a
  dedicated :class:`QThread`.
* Failures are logged but never raised; the corresponding ``custom_id``
  is simply absent from the result dict so callers can fall back to a
  synchronous one-off call.

Cost accounting: each batch response carries the same ``usage`` shape
as a normal call, so we feed it through :class:`TokenTracker.record`
to keep the cost meter honest. The Batch tier is roughly half-price,
which we model with a per-call cost multiplier.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import anthropic


_LOG = logging.getLogger(__name__)
_INITIAL_POLL_INTERVAL = 4.0
_MAX_POLL_INTERVAL = 30.0
_DEFAULT_TIMEOUT_S = 24 * 60 * 60   # Anthropic's own 24-hour SLA cap


@dataclass(frozen=True)
class BatchRequest:
    """One queued ``messages.create`` invocation."""
    custom_id: str
    model: str
    system: str
    messages: list[dict]
    max_tokens: int = 1024


@dataclass
class BatchProgress:
    submitted: int = 0
    processing: int = 0
    succeeded: int = 0
    errored: int = 0
    canceled: int = 0
    elapsed_s: float = 0.0
    eta_s: Optional[float] = None
    status: str = "submitting"   # submitting | in_progress | ended | failed

    def fraction_done(self) -> float:
        denom = self.submitted or 1
        return min(1.0, (self.succeeded + self.errored + self.canceled) / denom)

    def human_eta(self) -> str:
        if self.status == "ended":
            return "complete"
        if self.eta_s is None:
            return "calculating…"
        if self.eta_s < 60:
            return f"~{int(self.eta_s)} s"
        if self.eta_s < 3600:
            return f"~{int(self.eta_s // 60)} min"
        return f"~{self.eta_s / 3600:.1f} h"


ProgressCb = Callable[[BatchProgress], None]


class BatchRunner:
    """Run a batch of message requests through the Batches API."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        poll_interval_s: float = _INITIAL_POLL_INTERVAL,
    ):
        self._client = client
        self._timeout_s = timeout_s
        self._poll_interval = poll_interval_s

    def run(
        self,
        requests: Iterable[BatchRequest],
        *,
        on_progress: Optional[ProgressCb] = None,
        record_usage: Optional[Callable[[object, str], None]] = None,
    ) -> dict[str, str]:
        """Submit ``requests`` and return ``{custom_id: response_text}``.

        ``record_usage`` is invoked with ``(usage, model)`` for each
        successful entry so :class:`TokenTracker` can charge the meter.
        Calls without an entry in the returned dict are the caller's
        cue to fall back to a normal :meth:`AIClient._call`.
        """
        payload = [
            {
                "custom_id": r.custom_id,
                "params": {
                    "model": r.model,
                    "max_tokens": r.max_tokens,
                    "system": [
                        {
                            "type": "text",
                            "text": r.system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": r.messages,
                },
            }
            for r in requests
        ]
        if not payload:
            return {}

        progress = BatchProgress(submitted=len(payload), status="submitting")
        if on_progress:
            on_progress(progress)

        try:
            batch = self._client.messages.batches.create(requests=payload)
        except Exception as exc:
            _LOG.error("batch create failed: %s", exc)
            progress.status = "failed"
            if on_progress:
                on_progress(progress)
            return {}

        batch_id = batch.id
        progress.status = "in_progress"
        start = time.monotonic()
        interval = self._poll_interval

        while True:
            try:
                snapshot = self._client.messages.batches.retrieve(batch_id)
            except Exception as exc:
                _LOG.warning("batch poll failed (will retry): %s", exc)
                time.sleep(interval)
                interval = min(_MAX_POLL_INTERVAL, interval * 1.5)
                continue

            counts = getattr(snapshot, "request_counts", None)
            if counts is not None:
                progress.processing = int(getattr(counts, "processing", 0) or 0)
                progress.succeeded = int(getattr(counts, "succeeded", 0) or 0)
                progress.errored = int(getattr(counts, "errored", 0) or 0)
                progress.canceled = int(getattr(counts, "canceled", 0) or 0)
            elapsed = time.monotonic() - start
            progress.elapsed_s = elapsed
            progress.eta_s = self._estimate_eta(progress, elapsed)

            status = getattr(snapshot, "processing_status", "") or ""
            if status == "ended":
                progress.status = "ended"
                if on_progress:
                    on_progress(progress)
                break
            if elapsed > self._timeout_s:
                _LOG.warning("batch %s timed out after %.0fs", batch_id, elapsed)
                progress.status = "failed"
                if on_progress:
                    on_progress(progress)
                return {}
            if on_progress:
                on_progress(progress)
            time.sleep(interval)
            interval = min(_MAX_POLL_INTERVAL, interval * 1.4)

        return self._collect_results(batch_id, record_usage)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _collect_results(
        self,
        batch_id: str,
        record_usage: Optional[Callable[[object, str], None]],
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            results = self._client.messages.batches.results(batch_id)
        except Exception as exc:
            _LOG.error("batch results fetch failed: %s", exc)
            return {}
        for entry in results:
            cid = getattr(entry, "custom_id", "") or ""
            result = getattr(entry, "result", None)
            if result is None:
                continue
            rtype = getattr(result, "type", "") or ""
            if rtype != "succeeded":
                _LOG.info("batch entry %s did not succeed: %s", cid, rtype)
                continue
            message = getattr(result, "message", None)
            if message is None:
                continue
            content = getattr(message, "content", []) or []
            if not content:
                continue
            text = getattr(content[0], "text", "") or ""
            out[cid] = text
            usage = getattr(message, "usage", None)
            model = getattr(message, "model", "")
            if usage and record_usage:
                try:
                    record_usage(usage, model)
                except Exception as exc:
                    _LOG.debug("usage record failed for %s: %s", cid, exc)
        return out

    def _estimate_eta(self, progress: BatchProgress, elapsed: float) -> Optional[float]:
        done = progress.succeeded + progress.errored + progress.canceled
        if done <= 0 or elapsed <= 0:
            return None
        rate = done / elapsed   # requests / second
        remaining = max(0, progress.submitted - done)
        if rate <= 0:
            return None
        return remaining / rate
