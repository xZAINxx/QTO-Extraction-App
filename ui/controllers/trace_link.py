"""TraceLink — bidirectional binding between QtoDataTable rows and PDFViewer regions.

Wave 4 commit 6 of the dapper-pebble plan ("trace-back overlay"). When a
row is selected in the table, the PDF viewer jumps to its source page and
pulses a highlight on the row's bbox. When a region is clicked on the PDF
canvas, the table scrolls to the corresponding row and selects it.

The controller owns no Qt widgets of its own — it only wires signals between
an existing :class:`QtoDataTable` and an existing :class:`PDFViewer`. This
keeps it cheap to construct in tests with mocked counterparts.

A small ``_dispatch_lock`` flag breaks the row → canvas → row feedback loop
that would otherwise fire when ``jump_to_row`` calls ``selectRow`` and the
selection model promptly re-emits ``currentRowChanged``. The lock is held
just long enough (50ms) for Qt to drain the pending selection signal.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


Bbox = Tuple[float, float, float, float]


class TraceLink(QObject):
    """Two-way binding between a QtoDataTable and a PDFViewer."""

    # Source-model row index. Emitted after the canvas has been told to jump.
    row_focused = pyqtSignal(int)
    # (page_num, bbox) — emitted after a canvas click resolves to a table row.
    region_focused = pyqtSignal(int, tuple)

    def __init__(self, table, pdf_viewer, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._table = table
        self._pdf = pdf_viewer
        # Cache the model/proxy/view triple. They're methods on QtoDataTable —
        # caching once keeps the hot paths readable and avoids repeated lookups.
        self._model = table.model()
        self._proxy = table.proxy()
        self._view = table.view()
        self._dispatch_lock = False
        self._wire_signals()

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        sel_model = self._view.selectionModel()
        if sel_model is not None:
            sel_model.currentRowChanged.connect(self._on_row_selected)

    # ------------------------------------------------------------------
    # Row → canvas
    # ------------------------------------------------------------------

    def _on_row_selected(self, current, _previous) -> None:
        if self._dispatch_lock:
            return
        if current is None or not current.isValid():
            return
        source_idx = self._proxy.mapToSource(current)
        if not source_idx.isValid():
            return
        row = self._model.row_at(source_idx.row())
        page = int(row.source_page or 0)
        if page > 0 and hasattr(self._pdf, "go_to_page"):
            self._pdf.go_to_page(page)
            if row.bbox and hasattr(self._pdf, "pulse_highlight"):
                self._dispatch_lock = True
                self._pdf.pulse_highlight(page, row.bbox)
                QTimer.singleShot(50, self._release_lock)
        self.row_focused.emit(source_idx.row())

    # ------------------------------------------------------------------
    # Canvas → row
    # ------------------------------------------------------------------

    def jump_to_row(self, page_num: int, bbox: Bbox) -> None:
        """Find a row whose bbox overlaps ``bbox`` on ``page_num``; select it.

        Called by ``PDFViewer.region_clicked``. The first matching row wins.
        Header rows have no bbox by convention, so they're skipped naturally.
        """
        if self._dispatch_lock:
            return
        for source_row, row in enumerate(self._model.rows()):
            if int(row.source_page or 0) != int(page_num):
                continue
            if not row.bbox:
                continue
            if not _bbox_overlap(row.bbox, bbox):
                continue
            self._dispatch_lock = True
            try:
                source_idx = self._model.index(source_row, 0)
                proxy_idx = self._proxy.mapFromSource(source_idx)
                if proxy_idx.isValid():
                    self._view.selectRow(proxy_idx.row())
                    self._view.scrollTo(proxy_idx)
            finally:
                QTimer.singleShot(50, self._release_lock)
            self.region_focused.emit(int(page_num), tuple(bbox))
            return

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _release_lock(self) -> None:
        self._dispatch_lock = False


def _bbox_overlap(a: Bbox, b: Bbox) -> bool:
    """Axis-aligned bbox intersection test (inclusive of touching edges)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


__all__ = ["TraceLink"]
