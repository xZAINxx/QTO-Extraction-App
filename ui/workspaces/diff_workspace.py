"""DiffWorkspace — promote SetDiffDialog into a workspace tab.

Wave 5 commit 7 of the dapper-pebble plan. Mirrors the layout of the
legacy ``SetDiffDialog`` modal but lives inside a ``QTabWidget`` instead
of a top-level ``QDialog``. Heavy widgets (``_MiniPdfView``) and the
diff worker (``_DiffWorker``) are imported from ``ui.set_diff_view`` —
no PDF rendering or QThread plumbing is duplicated.

Adds the $-impact column called out in the plan: when the workspace
also has the current ``QTORow`` set, each changed sheet's label
includes the dollar value of rows originating on the affected page
(``qty * unit_price``). Without rows the label degrades gracefully to
just the change count.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import fitz
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QSplitter, QVBoxLayout, QWidget,
)

from core.qto_row import QTORow
from core.set_diff import DiffCluster, PageDiff, SetDiffResult, changed_page_numbers
from ui.components import Button
from ui.set_diff_view import _DiffWorker, _MiniPdfView
from ui.theme import tokens

_LOG = logging.getLogger(__name__)
_TOPBAR_HEIGHT = 44
_SHEET_LIST_WIDTH = 240
_SUMMARY_WIDTH = 320

_RED = QColor(239, 68, 68, 110)
_GREEN = QColor(16, 185, 129, 110)
_AMBER = QColor(245, 158, 11, 110)


class DiffWorkspace(QWidget):
    """Embedded What-Changed workspace.

    rerun_requested fires with a ``SetDiffResult`` when the estimator
    confirms the partial re-extract dialog. ``open_compare`` kicks off a
    diff. ``set_existing_rows`` feeds the current row set so the
    $-impact column can be calculated. ``clear`` / ``is_running`` are
    lifecycle hooks.
    """

    rerun_requested = pyqtSignal(object)  # SetDiffResult
    _YES = QMessageBox.StandardButton.Yes  # aliased for monkey-patching in tests
    _STATUS_ICON = {
        "modified": "✏", "added": "✚", "removed": "✗",
        "structural": "⚠", "unchanged": "·",
    }

    def __init__(self, config: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config or {}
        self._existing_rows: list[QTORow] = []
        self._result: Optional[SetDiffResult] = None
        self._old_doc: Optional[fitz.Document] = None
        self._new_doc: Optional[fitz.Document] = None
        self._worker: Optional[object] = None
        self._thread: Optional[QThread] = None
        self._build_ui()

    # --- Layout ------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())
        outer.addWidget(self._build_body(), 1)

    def _build_topbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("diffTopBar")
        bar.setFixedHeight(_TOPBAR_HEIGHT)
        surface = tokens["color"]["bg"]["surface"]["2"]
        border = tokens["color"]["border"]["subtle"]
        bar.setStyleSheet(
            f"#diffTopBar {{ background: {surface}; border-bottom: 1px solid {border}; }}"
        )
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(
            tokens["space"][4], tokens["space"][2],
            tokens["space"][4], tokens["space"][2],
        )
        layout.setSpacing(tokens["space"][3])

        self._old_label = QLabel("Old: —", bar)
        self._old_label.setObjectName("diffOldFileLabel")
        self._old_label.setProperty("textSize", "body-sm")
        self._new_label = QLabel("New: —", bar)
        self._new_label.setObjectName("diffNewFileLabel")
        self._new_label.setProperty("textSize", "body-sm")
        self._status_label = QLabel("", bar)
        self._status_label.setObjectName("diffStatusLabel")
        self._status_label.setProperty("textSize", "body-sm")
        self._rerun_btn = Button(
            text="Re-extract changed pages", icon_name="arrows-clockwise",
            variant="primary", size="sm", parent=bar,
        )
        self._rerun_btn.setObjectName("diffRerunBtn")
        self._rerun_btn.setEnabled(False)
        self._rerun_btn.clicked.connect(self._on_rerun_clicked)

        layout.addWidget(self._old_label)
        layout.addWidget(self._new_label)
        layout.addStretch(1)
        layout.addWidget(self._status_label)
        layout.addWidget(self._rerun_btn)
        return bar

    def _build_body(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("diffSplitter")
        splitter.setHandleWidth(2)

        self._sheet_list = QListWidget(splitter)
        self._sheet_list.setObjectName("diffSheetList")
        self._sheet_list.setMinimumWidth(_SHEET_LIST_WIDTH)
        self._sheet_list.itemSelectionChanged.connect(self._on_sheet_selected)
        self._old_view = _MiniPdfView("OLD", parent=splitter)
        self._new_view = _MiniPdfView("NEW", parent=splitter)
        self._summary = QPlainTextEdit(splitter)
        self._summary.setObjectName("diffSummary")
        self._summary.setReadOnly(True)
        self._summary.setMinimumWidth(_SUMMARY_WIDTH)

        for w in (self._sheet_list, self._old_view, self._new_view, self._summary):
            splitter.addWidget(w)
        for i, factor in enumerate((0, 4, 4, 2)):
            splitter.setStretchFactor(i, factor)
        splitter.setSizes([_SHEET_LIST_WIDTH, 480, 480, _SUMMARY_WIDTH])
        return splitter

    # --- Public API --------------------------------------------------------

    def open_compare(
        self, old_path: str, new_path: str, ai_client: object = None,
    ) -> None:
        """Begin diffing ``old_path`` vs ``new_path`` in a worker thread."""
        if self.is_running():
            _LOG.warning("DiffWorkspace busy — ignoring concurrent open_compare")
            return
        self.clear()
        self._old_label.setText(f"Old: {os.path.basename(old_path)}")
        self._new_label.setText(f"New: {os.path.basename(new_path)}")
        self._status_label.setText("Diffing…")

        self._thread = QThread(self)
        self._worker = _DiffWorker(old_path, new_path, ai_client=ai_client)
        try:
            self._worker.moveToThread(self._thread)
        except Exception:
            pass  # MagicMock in tests has no-op moveToThread
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_diff_finished)
        self._worker.error.connect(self._on_diff_error)
        self._thread.start()

    def set_existing_rows(self, rows: list[QTORow]) -> None:
        """Cache the current row set so $-impact can be calculated."""
        self._existing_rows = list(rows or [])
        if self._result is not None:
            self._populate(self._result)

    def clear(self) -> None:
        """Reset all state — used between diffs and on tab close."""
        self._sheet_list.clear()
        self._summary.setPlainText("")
        self._old_label.setText("Old: —")
        self._new_label.setText("New: —")
        self._status_label.setText("")
        self._rerun_btn.setEnabled(False)
        self._result = None
        for attr in ("_old_doc", "_new_doc"):
            doc = getattr(self, attr)
            if doc is not None:
                doc.close()
                setattr(self, attr, None)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    # --- Worker callbacks --------------------------------------------------

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        self._status_label.setText(f"{msg} ({current}/{total})")

    def _on_diff_finished(self, result: SetDiffResult) -> None:
        self._result = result
        try:
            self._old_doc = fitz.open(result.old_pdf)
            self._new_doc = fitz.open(result.new_pdf)
        except Exception as exc:
            self._on_diff_error(f"Failed to open PDFs for preview: {exc}")
            return
        self._populate(result)
        self._status_label.setText(result.report_summary())
        self._rerun_btn.setEnabled(any(p.status != "unchanged" for p in result.pairs))
        self._cleanup_worker()

    def _on_diff_error(self, msg: str) -> None:
        self._status_label.setText(f"Diff failed: {msg}")
        self._cleanup_worker()

    def _cleanup_worker(self) -> None:
        if self._thread is not None:
            try:
                self._thread.quit()
                self._thread.wait(2000)
            except Exception:
                pass
        self._thread = None
        self._worker = None

    # --- Population & selection -------------------------------------------

    def _populate(self, result: SetDiffResult) -> None:
        self._sheet_list.clear()
        ordered = sorted(
            result.pairs, key=lambda p: (p.status == "unchanged", p.sheet_id),
        )
        for pair in ordered:
            item = QListWidgetItem(self._format_sheet_label(pair))
            item.setData(Qt.ItemDataRole.UserRole, pair)
            self._sheet_list.addItem(item)
        for i in range(self._sheet_list.count()):
            pair = self._sheet_list.item(i).data(Qt.ItemDataRole.UserRole)
            if pair.status != "unchanged":
                self._sheet_list.setCurrentRow(i)
                return
        if self._sheet_list.count():
            self._sheet_list.setCurrentRow(0)

    def _format_sheet_label(self, pair: PageDiff) -> str:
        """Build a sheet-list label: ``"<icon> <sheet_id> — N changes — $X"``."""
        icon = self._STATUS_ICON.get(pair.status, "·")
        change_count = len(pair.clusters) if pair.clusters else 0
        if pair.status in ("added", "removed", "structural") and change_count == 0:
            change_count = 1
        parts = [f"{icon} {pair.sheet_id}", f"{change_count} changes"]
        if self._existing_rows:
            parts.append(f"${self._dollar_impact_for(pair):,.0f}")
        return " — ".join(parts)

    def _dollar_impact_for(self, pair: PageDiff) -> float:
        """Sum ``qty * unit_price`` for all rows on the affected page(s)."""
        page_nums: set[int] = set()
        if pair.new_page:
            page_nums.add(int(pair.new_page))
        if pair.old_page:
            page_nums.add(int(pair.old_page))
        if not page_nums:
            return 0.0
        total = 0.0
        for row in self._existing_rows:
            if int(getattr(row, "source_page", 0) or 0) in page_nums:
                qty = float(getattr(row, "qty", 0.0) or 0.0)
                price = float(getattr(row, "unit_price", 0.0) or 0.0)
                total += qty * price
        return total

    def _on_sheet_selected(self) -> None:
        items = self._sheet_list.selectedItems()
        if not items or self._result is None:
            return
        pair: PageDiff = items[0].data(Qt.ItemDataRole.UserRole)
        self._old_view.show_page(self._old_doc, pair.old_page)
        self._new_view.show_page(self._new_doc, pair.new_page)
        whole = [DiffCluster(pdf_rect=fitz.Rect(0, 0, 1e6, 1e6), pixel_count=0)]
        if pair.status == "added" and self._new_doc is not None:
            self._new_view.overlay_clusters(whole, _GREEN)
        elif pair.status == "removed" and self._old_doc is not None:
            self._old_view.overlay_clusters(whole, _RED)
        elif pair.status == "modified":
            self._old_view.overlay_clusters(pair.clusters, _AMBER)
            self._new_view.overlay_clusters(pair.clusters, _AMBER)
        elif pair.status == "structural":
            self._old_view.overlay_clusters(whole, _AMBER)
        self._summary.setPlainText(self._format_report(pair))

    @staticmethod
    def _format_report(pair: PageDiff) -> str:
        lines = [
            f"Sheet: {pair.sheet_id}", f"Status: {pair.status}",
            f"Old page: {pair.old_page or '—'}", f"New page: {pair.new_page or '—'}", "",
        ]
        if pair.status in ("added", "removed", "structural"):
            lines.append({
                "added": "This sheet is new — no equivalent in the old set.",
                "removed": "This sheet was deleted from the new set.",
                "structural": (
                    "Could not align the pages reliably (large rotation, "
                    "scale, or content shift). Treated as fully changed."
                ),
            }[pair.status])
            return "\n".join(lines)
        if not pair.clusters:
            lines.append("No meaningful differences detected.")
            return "\n".join(lines)
        lines.append("Change clusters:")
        for i, c in enumerate(pair.clusters, start=1):
            r = c.pdf_rect
            tag = c.description or "(no description)"
            lines.append(
                f"  {i:>2}. {tag}\n"
                f"      bbox=({r.x0:.0f},{r.y0:.0f})→({r.x1:.0f},{r.y1:.0f}) "
                f"px≈{c.pixel_count}"
            )
        return "\n".join(lines)

    # --- Re-extract --------------------------------------------------------

    def _on_rerun_clicked(self) -> None:
        if self._result is None:
            return
        n = len(changed_page_numbers(self._result))
        if n == 0:
            QMessageBox.information(self, "Nothing changed", "All pages are unchanged.")
            return
        ok = QMessageBox.question(
            self, "Re-extract changed pages",
            f"Re-extract {n} changed page(s) and merge with cached unchanged rows?",
        )
        if ok == self._YES:
            self.rerun_requested.emit(self._result)


__all__ = ["DiffWorkspace"]
