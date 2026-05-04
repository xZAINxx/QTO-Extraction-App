"""ExtractionWorker — background QThread worker that runs the assembler.

Lifted verbatim from ``ui/main_window.py`` (Wave 0) so both the legacy and
new MainWindow can import it from one location. No logic change — the
worker only depends on ``self._pdf_path``, ``self._config``,
``self._app_dir`` and ``self._cancel``, never on MainWindow private state,
which is what made the move safe.
"""
from __future__ import annotations

import fitz  # type: ignore[import-untyped]
from PyQt6.QtCore import QObject, pyqtSignal

from ai.client import AIClient
from core.assembler import Assembler
from core.cache import ResultCache
from core.qto_row import QTORow  # noqa: F401  (kept for type hints in subclasses)
from core.token_tracker import TokenTracker


class ExtractionWorker(QObject):
    """Long-running extraction task. ``moveToThread`` to a ``QThread`` and
    connect ``thread.started`` to :py:meth:`run`. Emits row-by-row progress
    so the UI can stream rows into the table without waiting for the whole
    PDF to finish.
    """

    page_started = pyqtSignal(int)          # page_num — emitted before processing begins
    progress = pyqtSignal(int, int, str)    # (current, total, page_type)
    row_ready = pyqtSignal(list)            # list of QTORow for one page
    tokens_updated = pyqtSignal(int, int, int, int, int, float)
    by_model_updated = pyqtSignal(dict)
    finished = pyqtSignal(list, bool)       # (all_rows, from_cache)
    error = pyqtSignal(str)
    # Phase 7 — message, done?, error? for the cost-saver batch indicator.
    batch_status = pyqtSignal(str, bool, bool)

    def __init__(self, pdf_path: str, config: dict, app_dir: str):
        super().__init__()
        self._pdf_path = pdf_path
        self._config = config
        self._app_dir = app_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from core.validator import validate
            from parser.pdf_splitter import split_and_classify

            cache = ResultCache(self._config.get("cache_dir", "./cache"))
            tracker = TokenTracker()

            # Check cache first
            cached = cache.load(self._pdf_path)
            if cached is not None:
                self.finished.emit(cached, True)
                return

            def _emit(u):
                self.tokens_updated.emit(
                    u.input_tokens, u.output_tokens, u.cache_read_tokens,
                    u.cache_write_tokens, u.api_calls, u.estimated_cost_usd,
                )
                self.by_model_updated.emit({
                    m: (mu.api_calls, mu.cost_usd(m)) for m, mu in u.by_model.items()
                })
            tracker.on_update(_emit)

            mode = self._config.get("extraction_mode", "hybrid")
            if mode == "multi_agent":
                from ai.multi_agent_client import MultiAgentClient
                ai = MultiAgentClient(self._config, tracker)
            else:
                ai = AIClient(self._config, tracker)
            assembler = Assembler(self._config, ai, tracker)

            all_rows: list = []
            classifications: dict = {}

            doc = fitz.open(self._pdf_path)
            total = doc.page_count
            doc.close()

            for page, page_info in split_and_classify(self._pdf_path):
                if self._cancel:
                    break

                self.page_started.emit(page_info.page_num)
                self.progress.emit(page_info.page_num, total, page_info.page_type)
                classifications[str(page_info.page_num)] = {
                    "page_type": page_info.page_type,
                    "skip": page_info.skip,
                    "skip_reason": page_info.skip_reason,
                    "text": page_info.text[:200],
                }

                rows = assembler.process_page(page, page_info, self._pdf_path)
                all_rows.extend(rows)
                if rows:
                    self.row_ready.emit(rows)

            if not self._cancel:
                # Phase 7 — flush queued compose calls through the Batches API
                # before we sort/validate so descriptions are final.
                self._maybe_flush_batched(assembler, all_rows)
                grouped = assembler.sort_by_sheet(all_rows)
                validate(grouped)
                cache.save(self._pdf_path, grouped, classifications)
                self.finished.emit(grouped, False)

        except Exception as e:
            self.error.emit(str(e))

    def _maybe_flush_batched(self, assembler, rows: list) -> None:
        ai = getattr(assembler, "_ai", None)
        if ai is None or not getattr(ai, "cost_saver_mode", False):
            return
        pending = getattr(ai, "pending_compose_count", 0)
        if not pending:
            return

        self.batch_status.emit(
            f"Cost-saver: submitting {pending} description calls…", False, False,
        )

        def _on_progress(p):
            done = p.succeeded + p.errored + p.canceled
            if p.status == "ended":
                return
            if p.status == "failed":
                self.batch_status.emit(
                    "Cost-saver: batch failed, falling back to live calls.",
                    False, True,
                )
                return
            self.batch_status.emit(
                f"Cost-saver batch: {done}/{p.submitted} done · ETA {p.human_eta()}",
                False, False,
            )

        try:
            upgraded = assembler.flush_batched_compose(rows, on_progress=_on_progress)
        except Exception as exc:
            self.batch_status.emit(
                f"Cost-saver: flush failed ({exc}); descriptions kept as-is.",
                False, True,
            )
            return
        self.batch_status.emit(
            f"Cost-saver: composed {upgraded} description(s) via batch.",
            True, False,
        )


__all__ = ["ExtractionWorker"]
