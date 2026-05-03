"""Main application window — dark theme, sidebar + main area layout."""
import os
from pathlib import Path
from typing import Optional

import fitz
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSplitter, QFrame, QFileDialog, QMessageBox,
    QSizePolicy, QInputDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from ai.client import AIClient
from core.assembler import Assembler
from core.token_tracker import TokenTracker
from core.xlsx_exporter import export as export_xlsx
from parser.pdf_splitter import classify_page

from ui.theme import (
    CANVAS, SURFACE_1, SURFACE_2, TEXT_1, TEXT_2, TEXT_3,
    BORDER_HEX, INDIGO, SIDEBAR_WIDTH, STYLESHEET,
)
from ui.upload_panel import UploadPanel
from ui.stats_bar import StatsBar
from ui.progress_panel import ProgressPanel
from ui.results_table import ResultsTable
from ui.assembly_palette import AssemblyPalette
from ui.pdf_viewer import PDFViewer
from ui.cost_meter import CostMeter
from ui.pattern_search_dialog import PatternSearchDialog
from ui.set_diff_view import SetDiffDialog
from ui.chat_panel import ChatPanel
from core.set_diff import (
    SetDiffResult, changed_page_numbers, merge_partial_rerun,
)
from core.qto_row import QTORow
from core.cache import ResultCache
from core.assembly_engine import AssemblyEngine, AssemblyInput


class ExtractionWorker(QObject):
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

            all_rows: list[QTORow] = []
            classifications = {}

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


class MainWindow(QMainWindow):
    def __init__(self, config: dict, app_dir: str):
        super().__init__()
        self._config = config
        self._app_dir = app_dir
        self._pdf_path: Optional[str] = None
        self._rows: list[QTORow] = []
        self._project_meta: dict = {}
        self._worker: Optional[ExtractionWorker] = None
        self._thread: Optional[QThread] = None
        self._cache: Optional[ResultCache] = None

        self.setWindowTitle("Zeconic QTO Tool")
        self.resize(1440, 900)
        self.setMinimumSize(1024, 680)
        self.setStyleSheet(STYLESHEET)

        self._api_key_missing = not (
            os.environ.get("ANTHROPIC_API_KEY") or config.get("anthropic_api_key")
        )
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 16)
        sidebar_layout.setSpacing(12)

        # Title
        title = QLabel("Zeconic QTO")
        title_font = QFont(".AppleSystemUIFont")
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {TEXT_1};")
        sidebar_layout.addWidget(title)

        # Upload panel
        self._upload_panel = UploadPanel()
        self._upload_panel.pdf_selected.connect(self._on_pdf_selected)
        self._upload_panel.project_meta_changed.connect(self._on_meta_changed)
        self._upload_panel.cost_saver_toggled.connect(self._on_cost_saver_toggled)
        self._upload_panel.set_cost_saver(bool(self._config.get("cost_saver_mode", False)))
        sidebar_layout.addWidget(self._upload_panel)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER_HEX};")
        sidebar_layout.addWidget(sep)

        # Stats
        self._stats = StatsBar()
        self._stats.set_mode(self._config.get("extraction_mode", "hybrid"))
        sidebar_layout.addWidget(self._stats)

        # Tool buttons (Phase 4) — Pattern Search, Compare Sets, Chat, Assemblies.
        sidebar_layout.addWidget(self._build_tool_buttons())

        # Assembly palette (Phase 3) — drag-and-drop quantity assemblies.
        # Visibility toggled by the Assemblies tool button.
        self._assembly_engine = AssemblyEngine()
        self._assembly_palette = AssemblyPalette(self._assembly_engine)
        self._assembly_palette.row_created.connect(self._on_assembly_row)
        self._assembly_palette.save_requested.connect(self._save_selected_as_assembly)
        sidebar_layout.addWidget(self._assembly_palette, 1)

        if self._api_key_missing:
            key_warn = QLabel("⚠ Set ANTHROPIC_API_KEY to enable AI extraction")
            key_warn.setWordWrap(True)
            key_warn.setStyleSheet(
                "color: #F59E0B; font-size: 11px; padding: 6px 4px;"
            )
            sidebar_layout.addWidget(key_warn)

        sidebar_layout.addStretch()

        # Action buttons
        self._run_btn = QPushButton("Run QTO")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run_extraction)
        sidebar_layout.addWidget(self._run_btn)

        self._export_btn = QPushButton("Export .xlsx")
        self._export_btn.setObjectName("exportBtn")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        sidebar_layout.addWidget(self._export_btn)

        # ── Main area ──────────────────────────────────────────────────────
        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(2)
        self._splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {BORDER_HEX}; }}"
        )

        # Left: embedded PDF viewer.
        self._pdf_viewer = PDFViewer()
        self._pdf_viewer.region_captured.connect(self._on_pdf_region_captured)
        self._splitter.addWidget(self._pdf_viewer)

        # Right: results + progress stacked vertically.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 0)
        right_layout.setSpacing(8)

        self._results = ResultsTable()
        self._results.row_jump_requested.connect(self._on_jump_page)
        self._results.save_as_assembly_requested.connect(self._save_row_as_assembly)
        right_layout.addWidget(self._results, 1)

        self._progress = ProgressPanel()
        self._progress.retry_page.connect(self._retry_page)
        self._progress.setMaximumHeight(220)
        right_layout.addWidget(self._progress)

        self._splitter.addWidget(right)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([720, 720])
        main_layout.addWidget(self._splitter, 1)

        # Pinned cost meter footer (Phase 4).
        self._cost_meter = CostMeter()
        self._cost_meter.set_mode(self._config.get("extraction_mode", "hybrid"))
        main_layout.addWidget(self._cost_meter)

        root.addWidget(sidebar)
        root.addWidget(main_area, 1)

        self._pattern_dialog: Optional[PatternSearchDialog] = None
        self._chat_panel: Optional[ChatPanel] = None
        self._chat_tracker: Optional[TokenTracker] = None
        self._chat_ai: Optional[AIClient] = None

    # ── Helpers ────────────────────────────────────────────────────────────

    def _data_row_count(self) -> int:
        return sum(1 for r in self._rows if not r.is_header_row)

    def _build_tool_buttons(self) -> QFrame:
        """Phase 4 — 4 sidebar tool buttons (icon-only, dark)."""
        frame = QFrame()
        frame.setObjectName("toolBar")
        frame.setStyleSheet(
            f"#toolBar {{ background: transparent; }}"
            f"QPushButton {{ background: {SURFACE_2}; color: {TEXT_2}; "
            f"border: 1px solid {BORDER_HEX}; padding: 6px 4px; border-radius: 6px; "
            f"font-size: 11px; }}"
            f"QPushButton:hover {{ background: {INDIGO}; color: white; border-color: {INDIGO}; }}"
            f"QPushButton:disabled {{ color: {TEXT_3}; }}"
        )
        h = QHBoxLayout(frame)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        self._tool_pattern = QPushButton("Pattern")
        self._tool_pattern.setToolTip("Pattern Search — find every instance of a symbol")
        self._tool_pattern.clicked.connect(self._open_pattern_search)
        self._tool_pattern.setEnabled(False)

        self._tool_diff = QPushButton("Compare")
        self._tool_diff.setToolTip("Compare Sets — pick a revised PDF and diff it against the loaded one")
        self._tool_diff.clicked.connect(self._open_compare_sets)
        self._tool_diff.setEnabled(False)

        self._tool_chat = QPushButton("Chat")
        self._tool_chat.setToolTip("Chat — ask natural-language questions about the current takeoff")
        self._tool_chat.clicked.connect(self._open_chat)
        self._tool_chat.setEnabled(False)

        self._tool_assemblies = QPushButton("Assemblies")
        self._tool_assemblies.setToolTip("Toggle the assembly palette")
        self._tool_assemblies.setCheckable(True)
        self._tool_assemblies.setChecked(True)
        self._tool_assemblies.toggled.connect(self._toggle_assembly_palette)

        for b in (self._tool_pattern, self._tool_diff, self._tool_chat, self._tool_assemblies):
            h.addWidget(b)
        return frame

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_pdf_selected(self, path: str):
        self._pdf_path = path
        self._run_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        self._results.load_rows([])

        # Load into the embedded viewer & enable pattern-search + diff tools.
        if self._pdf_viewer.load(path):
            self._tool_pattern.setEnabled(True)
            self._tool_diff.setEnabled(True)

        if self._cache:
            self._cache.close()
        self._cache = ResultCache(self._config.get("cache_dir", "./cache"))
        cached = self._cache.load(path)
        if cached:
            self._rows = cached
            self._results.load_rows(cached)
            self._stats.update_rows(self._data_row_count())
            self._stats.show_cache_hit(True)
            self._export_btn.setEnabled(True)
            if self._chat_panel is not None:
                self._chat_panel.set_rows(self._rows)
        self._tool_chat.setEnabled(
            self._data_row_count() > 0 and not self._api_key_missing
        )

    def _on_meta_changed(self, meta: dict):
        self._project_meta = meta

    def _on_cost_saver_toggled(self, enabled: bool):
        # Update the live config so the next ExtractionWorker / AIClient
        # picks it up. Existing AIClient instances (chat, diff) read
        # ``cost_saver_mode`` once at construction so a flip mid-session
        # only affects subsequent runs — which is the desired behaviour
        # since flipping mid-extraction would orphan the queue.
        self._config["cost_saver_mode"] = bool(enabled)

    def _run_extraction(self):
        if not self._pdf_path:
            return
        if self._api_key_missing:
            QMessageBox.warning(
                self,
                "API Key Required",
                "Set the ANTHROPIC_API_KEY environment variable to enable AI extraction.\n\n"
                "Add it to a .env file in the app directory or export it in your shell.",
            )
            return

        doc = fitz.open(self._pdf_path)
        total = doc.page_count
        doc.close()

        self._progress.init_pages(total)
        self._progress.set_batch_status("")
        self._rows = []
        self._results.load_rows([])
        self._stats.show_cache_hit(False)
        self._run_btn.setText("Cancel")
        self._run_btn.setObjectName("cancelBtn")
        self._run_btn.setStyleSheet("")
        self._run_btn.clicked.disconnect()
        self._run_btn.clicked.connect(self._cancel_extraction)

        self._worker = ExtractionWorker(self._pdf_path, self._config, self._app_dir)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._worker.page_started.connect(self._on_page_started)
        self._worker.progress.connect(self._on_page_progress)
        self._worker.row_ready.connect(self._on_rows_ready)
        self._worker.tokens_updated.connect(self._on_tokens)
        self._worker.by_model_updated.connect(self._on_by_model)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.batch_status.connect(self._on_batch_status)
        self._thread.started.connect(self._worker.run)

        self._thread.start()

    def _cancel_extraction(self):
        if self._worker:
            self._worker.cancel()
        self._reset_run_btn()

    def _reset_run_btn(self):
        self._run_btn.setText("Run QTO")
        self._run_btn.setObjectName("")
        self._run_btn.setStyleSheet("")
        self._run_btn.clicked.disconnect()
        self._run_btn.clicked.connect(self._run_extraction)

    def _on_page_started(self, page_num: int):
        self._progress.set_page_running(page_num)

    def _on_page_progress(self, current: int, total: int, page_type: str):
        self._progress.set_page_status(current, "done", page_type)
        self._stats.update_progress(current, total)

    def _on_rows_ready(self, rows: list):
        self._rows.extend(rows)
        self._results.load_rows(self._rows)
        self._stats.update_rows(self._data_row_count())

    def _on_tokens(self, inp: int, out: int, cr: int, cw: int, calls: int, cost: float):
        self._stats.update_tokens(inp, out, cr, cw, calls, cost)
        self._cost_meter.update_tokens(inp, out, cr, cw, calls, cost)

    def _on_by_model(self, by_model: dict):
        self._stats.update_by_model(by_model)
        self._cost_meter.update_by_model(by_model)

    def _on_finished(self, rows: list, from_cache: bool):
        self._rows = rows
        self._results.load_rows(rows)
        self._progress.set_complete()
        self._stats.show_cache_hit(from_cache)
        self._stats.update_rows(self._data_row_count())
        self._export_btn.setEnabled(True)
        self._tool_chat.setEnabled(self._data_row_count() > 0 and not self._api_key_missing)
        if self._chat_panel is not None:
            self._chat_panel.set_rows(self._rows)
        self._reset_run_btn()
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Extraction Error", msg)
        self._reset_run_btn()
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)

    def _on_batch_status(self, message: str, done: bool, error: bool):
        self._progress.set_batch_status(message, done=done, error=error)

    def _export(self):
        if not self._rows:
            return
        try:
            pdf_stem = Path(self._pdf_path).stem if self._pdf_path else "export"
            out = export_xlsx(
                self._rows,
                template_path=self._config.get("template_path", "./ESTIMATE_FORMAT___GC.xlsx"),
                output_dir=self._config.get("output_dir", "./output"),
                pdf_stem=pdf_stem,
                project_meta=self._project_meta or None,
            )
            QMessageBox.information(self, "Exported", f"Saved to:\n{out}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _on_jump_page(self, page_num: int, sheet: str):
        if not self._pdf_path or not page_num:
            return
        # Phase 4: navigate the embedded viewer instead of launching Preview.
        self._pdf_viewer.go_to_page(page_num)

    # ── Tool buttons ──────────────────────────────────────────────────────

    def _toggle_assembly_palette(self, visible: bool):
        self._assembly_palette.setVisible(visible)

    def _open_pattern_search(self):
        if not self._pdf_path:
            return
        if self._pattern_dialog is not None and self._pattern_dialog.isVisible():
            self._pattern_dialog.raise_()
            self._pattern_dialog.activateWindow()
            return
        self._pattern_dialog = PatternSearchDialog(self._pdf_path, self)
        self._pattern_dialog.request_capture.connect(
            lambda: self._pdf_viewer.set_capture_mode(True)
        )
        self._pattern_dialog.cancel_capture.connect(
            lambda: self._pdf_viewer.set_capture_mode(False)
        )
        self._pattern_dialog.rows_accepted.connect(self._on_pattern_rows)
        self._pattern_dialog.finished.connect(
            lambda _result: self._pdf_viewer.set_capture_mode(False)
        )
        self._pdf_viewer.set_capture_mode(True)
        self._pattern_dialog.show()

    def _on_pdf_region_captured(self, region):
        if self._pattern_dialog is not None:
            self._pattern_dialog.accept_captured_region(region)
            self._pdf_viewer.set_capture_mode(False)

    def _on_pattern_rows(self, rows: list):
        for r in rows:
            self._rows.append(r)
        self._results.load_rows(self._rows)
        self._stats.update_rows(self._data_row_count())
        self._export_btn.setEnabled(True)

    def _open_compare_sets(self):
        if not self._pdf_path:
            return
        new_pdf, _ = QFileDialog.getOpenFileName(
            self, "Pick the revised PDF to compare against",
            str(Path(self._pdf_path).parent), "PDF (*.pdf)",
        )
        if not new_pdf or new_pdf == self._pdf_path:
            return
        ai = None
        if not self._api_key_missing:
            try:
                # Reuse a temporary tracker so diff descriptions still hit the cost meter.
                tracker = TokenTracker()
                tracker.on_update(lambda u: self._on_tokens(
                    u.input_tokens, u.output_tokens, u.cache_read_tokens,
                    u.cache_write_tokens, u.api_calls, u.estimated_cost_usd,
                ))
                ai = AIClient(self._config, tracker)
            except Exception as exc:
                QMessageBox.warning(self, "AI unavailable",
                                    f"Continuing without diff descriptions: {exc}")
                ai = None
        dlg = SetDiffDialog(self._pdf_path, new_pdf, ai_client=ai, parent=self)
        dlg.rerun_requested.connect(
            lambda result: self._partial_rerun(new_pdf, result)
        )
        dlg.show()
        # Keep a reference so it isn't GC'd.
        self._diff_dialog = dlg

    def _partial_rerun(self, new_pdf_path: str, result: "SetDiffResult"):
        """Run the assembler ONLY on the changed pages of the revised PDF
        and merge the result with our cached row set.
        """
        if not self._rows:
            QMessageBox.information(
                self, "Nothing cached",
                "Run the takeoff on the original PDF first, then re-run after a diff.",
            )
            return
        try:
            tracker = TokenTracker()
            tracker.on_update(lambda u: self._on_tokens(
                u.input_tokens, u.output_tokens, u.cache_read_tokens,
                u.cache_write_tokens, u.api_calls, u.estimated_cost_usd,
            ))
            ai = AIClient(self._config, tracker)
            assembler = Assembler(self._config, ai, tracker)

            doc = fitz.open(new_pdf_path)
            new_rows: list = []
            for page_num in sorted(changed_page_numbers(result)):
                page = doc[page_num - 1]
                text = page.get_text("text") or ""
                page_info = classify_page(page_num, text)
                new_rows.extend(assembler.process_page(page, page_info, new_pdf_path))
            doc.close()

            merged = merge_partial_rerun(
                self._rows, new_rows,
                changed_sheet_ids=set(result.changed_sheet_ids()),
            )
            grouped = assembler.sort_by_sheet(merged)
            self._rows = grouped
            self._results.load_rows(grouped)
            self._stats.update_rows(self._data_row_count())
            QMessageBox.information(
                self, "Re-extract complete",
                f"Re-extracted {len(new_rows)} rows across "
                f"{len(changed_page_numbers(result))} changed pages.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Re-extract failed", str(exc))

    def _open_chat(self):
        if self._api_key_missing:
            QMessageBox.warning(
                self, "API Key Required",
                "Set ANTHROPIC_API_KEY before using Chat — it talks to Sonnet.",
            )
            return
        if self._chat_panel is None:
            # Persistent tracker so chat costs roll into the same cost meter
            # even if the user opens, closes, and re-opens the panel.
            self._chat_tracker = TokenTracker()
            self._chat_tracker.on_update(lambda u: self._on_tokens(
                u.input_tokens, u.output_tokens, u.cache_read_tokens,
                u.cache_write_tokens, u.api_calls, u.estimated_cost_usd,
            ))
            self._chat_tracker.on_update(lambda u: self._on_by_model({
                m: (mu.api_calls, mu.cost_usd(m)) for m, mu in u.by_model.items()
            }))
            try:
                self._chat_ai = AIClient(self._config, self._chat_tracker)
            except Exception as exc:
                QMessageBox.critical(self, "Chat unavailable", str(exc))
                return
            self._chat_panel = ChatPanel(self._chat_ai, parent=self)
            self._chat_panel.citation_clicked.connect(self._on_chat_citation)
        self._chat_panel.set_rows(self._rows)
        self._chat_panel.show()
        self._chat_panel.raise_()
        self._chat_panel.activateWindow()

    def _on_chat_citation(self, page: int, sheet: str):
        if page > 0:
            self._on_jump_page(page, sheet)

    def _on_assembly_row(self, row: QTORow):
        """Append an assembly-produced row to the active takeoff."""
        self._rows.append(row)
        self._results.load_rows(self._rows)
        self._stats.update_rows(self._data_row_count())
        self._export_btn.setEnabled(True)

    def _save_row_as_assembly(self, idx: int):
        """Triggered by the 'Save as Assembly…' context-menu action."""
        row = self._results.row_at_index(idx)
        if row is None or row.is_header_row:
            return
        self._do_save_assembly(row)

    def _save_selected_as_assembly(self):
        """Triggered by the '+' button on the assembly palette."""
        selected = self._results.selected_data_row()
        if selected is None:
            QMessageBox.information(
                self, "Save as Assembly",
                "Select a row in the table first, then click '+'."
            )
            return
        self._do_save_assembly(selected)

    def _do_save_assembly(self, selected: QTORow):
        # Use the selected row's description as a starter template; the
        # user can edit the YAML by hand to add inputs later.
        suggested_key = (selected.description.strip().split()[:4] or ["custom"])
        key_default = "_".join(w.lower() for w in suggested_key) or "custom_assembly"
        key, ok = QInputDialog.getText(self, "New Assembly", "Key (no spaces):", text=key_default)
        if not ok or not key:
            return
        name, ok = QInputDialog.getText(self, "New Assembly", "Display name:",
                                        text=selected.description[:40])
        if not ok or not name:
            return
        try:
            path = self._assembly_engine.save_assembly(
                key=key,
                name=name,
                trade=selected.trade_division.split()[-1].lower() if selected.trade_division else "general",
                csi_division=selected.trade_division,
                units=selected.units or "EA",
                description_template=selected.description,
                inputs=[
                    AssemblyInput(name="qty", label="Quantity",
                                  type="number", default=selected.qty or 0),
                ],
            )
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", str(e))
            return
        self._assembly_palette.reload()
        QMessageBox.information(self, "Saved", f"New assembly saved to:\n{path}")

    def _retry_page(self, page_num: int):
        if not self._pdf_path:
            return
        # Re-run extraction for a single page and merge result back
        try:
            tracker = TokenTracker()
            ai = AIClient(self._config, tracker)
            assembler = Assembler(self._config, ai, tracker)
            doc = fitz.open(self._pdf_path)
            page = doc[page_num - 1]
            text = page.get_text("text") or ""
            page_info = classify_page(page_num, text)
            new_rows = assembler.process_page(page, page_info, self._pdf_path)
            doc.close()
            # Remove old rows from this page and append new ones
            self._rows = [r for r in self._rows if r.source_page != page_num]
            self._rows.extend(new_rows)
            self._results.load_rows(self._rows)
            self._stats.update_rows(self._data_row_count())
        except Exception as e:
            QMessageBox.warning(self, "Retry Failed", str(e))

    def closeEvent(self, event):
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        if self._cache:
            try:
                self._cache.close()
            except Exception:
                pass
        event.accept()
