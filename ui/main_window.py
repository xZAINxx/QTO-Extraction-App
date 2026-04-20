"""Main application window — dark theme, sidebar + main area layout."""
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSplitter, QFrame, QFileDialog, QMessageBox,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from ui.theme import (
    CANVAS, SURFACE_1, SURFACE_2, TEXT_1, TEXT_2, TEXT_3,
    BORDER_HEX, INDIGO, SIDEBAR_WIDTH, STYLESHEET,
)
from ui.upload_panel import UploadPanel
from ui.stats_bar import StatsBar
from ui.progress_panel import ProgressPanel
from ui.results_table import ResultsTable
from core.qto_row import QTORow
from core.cache import ResultCache


class ExtractionWorker(QObject):
    page_started = pyqtSignal(int)          # page_num — emitted before processing begins
    progress = pyqtSignal(int, int, str)    # (current, total, page_type)
    row_ready = pyqtSignal(list)            # list of QTORow for one page
    tokens_updated = pyqtSignal(int, int, int, int, int, float)
    finished = pyqtSignal(list, bool)       # (all_rows, from_cache)
    error = pyqtSignal(str)

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
            import yaml
            from core.token_tracker import TokenTracker
            from ai.client import AIClient
            from core.assembler import Assembler
            from core.validator import validate
            from parser.pdf_splitter import split_and_classify

            cache = ResultCache(self._config.get("cache_dir", "./cache"))
            tracker = TokenTracker()

            # Check cache first
            cached = cache.load(self._pdf_path)
            if cached is not None:
                self.finished.emit(cached, True)
                return

            tracker.on_update(lambda u: self.tokens_updated.emit(
                u.input_tokens, u.output_tokens, u.cache_read_tokens,
                u.cache_write_tokens, u.api_calls, u.estimated_cost_usd,
            ))

            ai = AIClient(self._config, tracker)
            assembler = Assembler(self._config, ai, tracker)

            all_rows: list[QTORow] = []
            classifications = {}

            import fitz
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
                grouped = assembler.sort_by_sheet(all_rows)
                validate(grouped)
                cache.save(self._pdf_path, grouped, classifications)
                self.finished.emit(grouped, False)

        except Exception as e:
            self.error.emit(str(e))


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
        sidebar_layout.addWidget(self._upload_panel)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER_HEX};")
        sidebar_layout.addWidget(sep)

        # Stats
        self._stats = StatsBar()
        self._stats.set_mode(self._config.get("extraction_mode", "hybrid"))
        sidebar_layout.addWidget(self._stats)

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
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        self._results = ResultsTable()
        self._results.row_jump_requested.connect(self._on_jump_page)
        main_layout.addWidget(self._results)

        self._progress = ProgressPanel()
        self._progress.retry_page.connect(self._retry_page)
        self._progress.setMaximumHeight(280)
        main_layout.addWidget(self._progress)

        root.addWidget(sidebar)
        root.addWidget(main_area, 1)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _data_row_count(self) -> int:
        return sum(1 for r in self._rows if not r.is_header_row)

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_pdf_selected(self, path: str):
        self._pdf_path = path
        self._run_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        self._results.load_rows([])

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

    def _on_meta_changed(self, meta: dict):
        self._project_meta = meta

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

        import fitz
        doc = fitz.open(self._pdf_path)
        total = doc.page_count
        doc.close()

        self._progress.init_pages(total)
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
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
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

    def _on_finished(self, rows: list, from_cache: bool):
        self._rows = rows
        self._results.load_rows(rows)
        self._progress.set_complete()
        self._stats.show_cache_hit(from_cache)
        self._stats.update_rows(self._data_row_count())
        self._export_btn.setEnabled(True)
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

    def _export(self):
        from core.xlsx_exporter import export
        if not self._rows:
            return
        try:
            pdf_stem = Path(self._pdf_path).stem if self._pdf_path else "export"
            out = export(
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
        try:
            if sys.platform == "darwin":
                # Open PDF first, then navigate to the specific page via AppleScript
                subprocess.Popen(["open", "-a", "Preview", self._pdf_path])
                script = (
                    f'tell application "Preview"\n'
                    f'  activate\n'
                    f'  delay 1\n'
                    f'  tell front document\n'
                    f'    go to page {page_num}\n'
                    f'  end tell\n'
                    f'end tell'
                )
                subprocess.Popen(["osascript", "-e", script])
        except Exception:
            pass

    def _retry_page(self, page_num: int):
        if not self._pdf_path:
            return
        # Re-run extraction for a single page and merge result back
        from ai.client import AIClient
        from core.assembler import Assembler
        from core.token_tracker import TokenTracker
        import fitz

        try:
            tracker = TokenTracker()
            ai = AIClient(self._config, tracker)
            assembler = Assembler(self._config, ai, tracker)
            doc = fitz.open(self._pdf_path)
            page = doc[page_num - 1]
            from parser.pdf_splitter import classify_page
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
