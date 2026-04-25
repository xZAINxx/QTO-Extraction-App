"""Set Diff viewer — Phase 5 deliverable.

A modal-ish dialog that shows the result of :func:`core.set_diff.diff_sets`:

    ┌─────────────────────────────────────────────────────────────────┐
    │ Old: Drawings_v1.pdf       New: Drawings_v2.pdf       [Re-extract]
    ├──────────────┬───────────────────┬──────────────────────────────┤
    │ Sheet list   │ OLD viewer  │ NEW viewer  │ "What Changed" panel │
    │  A-101 ✏     │  ┌────────┐ │  ┌────────┐ │  • Detail 4: window  │
    │  A-201 ✚     │  │ render │ │  │ render │ │    type changed       │
    │  A-301 ✗     │  └────────┘ │  └────────┘ │  • Schedule note ...  │
    └──────────────┴─────────────┴─────────────┴──────────────────────┘

Diff overlay: red rectangles = removed area (only in OLD), green =
added area (only in NEW), amber = modified area (in both). The
classification falls back to "modified" for paired pages and "added"/
"removed" for unpaired sheets.
"""
from __future__ import annotations

import logging
from typing import Optional

import fitz
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QRectF
from PyQt6.QtGui import QImage, QPixmap, QColor, QPen, QBrush
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QWidget, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QPlainTextEdit, QGraphicsView,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem, QFrame,
    QProgressBar, QMessageBox,
)

from core.set_diff import (
    diff_sets, SetDiffResult, PageDiff, DiffCluster, changed_page_numbers,
)
from ui.theme import (
    SURFACE_1, SURFACE_2, SURFACE_3, BORDER_HEX, TEXT_1, TEXT_2, TEXT_3,
    INDIGO, EMERALD,
)


_LOG = logging.getLogger(__name__)
_RENDER_DPI = 110

_RED   = QColor(239, 68, 68, 110)
_GREEN = QColor(16, 185, 129, 110)
_AMBER = QColor(245, 158, 11, 110)


class _DiffWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object)        # SetDiffResult
    error = pyqtSignal(str)

    def __init__(self, old_pdf: str, new_pdf: str, ai_client=None, describe: bool = True):
        super().__init__()
        self._old = old_pdf
        self._new = new_pdf
        self._ai = ai_client
        self._describe = describe
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            def _cb(c, t, msg):
                self.progress.emit(c, t, msg)
            result = diff_sets(
                self._old, self._new,
                ai_client=self._ai, progress=_cb, describe=self._describe,
            )
            if not self._cancel:
                self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class _MiniPdfView(QFrame):
    """One-page PDF view used in the OLD/NEW columns of the diff dialog."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("miniPdfView")
        self.setStyleSheet(
            f"#miniPdfView {{ background: {SURFACE_2}; border: 1px solid {BORDER_HEX}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel(label)
        header.setStyleSheet(
            f"background: {SURFACE_3}; color: {TEXT_2}; padding: 4px 8px; "
            f"border-bottom: 1px solid {BORDER_HEX}; font-weight: 600;"
        )
        layout.addWidget(header)

        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(QBrush(QColor(SURFACE_1)))
        self._view = QGraphicsView(self._scene)
        self._view.setStyleSheet(f"background: {SURFACE_1}; border: none;")
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._view, 1)

        self._page_item: Optional[QGraphicsPixmapItem] = None
        self._overlay_items: list[QGraphicsRectItem] = []
        self._render_zoom = _RENDER_DPI / 72.0

    def show_page(self, doc: Optional[fitz.Document], page_num: Optional[int]):
        for it in self._overlay_items:
            self._scene.removeItem(it)
        self._overlay_items.clear()
        if doc is None or page_num is None or page_num < 1 or page_num > doc.page_count:
            if self._page_item is not None:
                self._scene.removeItem(self._page_item)
                self._page_item = None
            self._scene.setSceneRect(QRectF(0, 0, 0, 0))
            return
        page = doc[page_num - 1]
        mat = fitz.Matrix(self._render_zoom, self._render_zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        ).copy()
        qpix = QPixmap.fromImage(img)
        if self._page_item is None:
            self._page_item = QGraphicsPixmapItem(qpix)
            self._scene.addItem(self._page_item)
        else:
            self._page_item.setPixmap(qpix)
        self._scene.setSceneRect(QRectF(qpix.rect()))
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def overlay_clusters(self, clusters: list[DiffCluster], color: QColor):
        for cluster in clusters:
            r = cluster.pdf_rect
            scene_rect = QRectF(
                r.x0 * self._render_zoom, r.y0 * self._render_zoom,
                (r.x1 - r.x0) * self._render_zoom, (r.y1 - r.y0) * self._render_zoom,
            )
            pen = QPen(QColor(color.red(), color.green(), color.blue()))
            pen.setWidthF(2.0)
            pen.setCosmetic(True)
            brush = QBrush(color)
            item = self._scene.addRect(scene_rect, pen, brush)
            item.setZValue(20)
            self._overlay_items.append(item)


class SetDiffDialog(QDialog):
    """Top-level dialog wired to a background diff worker."""

    rerun_requested = pyqtSignal(object)   # SetDiffResult — for partial re-extract

    def __init__(
        self,
        old_pdf: str,
        new_pdf: str,
        *,
        ai_client=None,
        describe: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Compare Drawing Sets")
        self.resize(1280, 820)
        self._old_pdf = old_pdf
        self._new_pdf = new_pdf
        self._ai = ai_client
        self._describe = describe
        self._result: Optional[SetDiffResult] = None
        self._old_doc: Optional[fitz.Document] = None
        self._new_doc: Optional[fitz.Document] = None
        self._worker: Optional[_DiffWorker] = None
        self._thread: Optional[QThread] = None
        self._build_ui()
        self._start_diff()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        old_lbl = QLabel(f"Old: {self._old_pdf}")
        new_lbl = QLabel(f"New: {self._new_pdf}")
        for lbl in (old_lbl, new_lbl):
            lbl.setStyleSheet(f"color: {TEXT_2}; font-size: 11px;")
        self._status = QLabel("Diffing…")
        self._status.setStyleSheet(f"color: {INDIGO}; font-weight: 600;")
        self._rerun_btn = QPushButton("Re-extract changed pages")
        self._rerun_btn.setEnabled(False)
        self._rerun_btn.clicked.connect(self._on_rerun_clicked)
        header.addWidget(old_lbl)
        header.addSpacing(16)
        header.addWidget(new_lbl)
        header.addStretch()
        header.addWidget(self._status)
        header.addWidget(self._rerun_btn)
        root.addLayout(header)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(6)
        self._progress.setTextVisible(False)
        root.addWidget(self._progress)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setHandleWidth(2)
        body.setStyleSheet(f"QSplitter::handle {{ background: {BORDER_HEX}; }}")

        # Sheet list
        self._sheet_list = QListWidget()
        self._sheet_list.setMinimumWidth(200)
        self._sheet_list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE_2}; color: {TEXT_1}; "
            f"border: 1px solid {BORDER_HEX}; }}"
        )
        self._sheet_list.itemSelectionChanged.connect(self._on_sheet_selected)
        body.addWidget(self._sheet_list)

        # OLD + NEW viewers
        viewers = QSplitter(Qt.Orientation.Horizontal)
        viewers.setHandleWidth(2)
        self._old_view = _MiniPdfView("OLD")
        self._new_view = _MiniPdfView("NEW")
        viewers.addWidget(self._old_view)
        viewers.addWidget(self._new_view)
        viewers.setSizes([1, 1])
        body.addWidget(viewers)

        # "What Changed" report
        report = QFrame()
        report.setObjectName("reportPanel")
        report.setStyleSheet(
            f"#reportPanel {{ background: {SURFACE_2}; border: 1px solid {BORDER_HEX}; }}"
        )
        rl = QVBoxLayout(report)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(6)
        rl.addWidget(self._labeled("What Changed"))
        self._report = QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setStyleSheet(
            f"QPlainTextEdit {{ background: {SURFACE_1}; color: {TEXT_1}; "
            f"border: 1px solid {BORDER_HEX}; font-family: 'SF Mono', Menlo, monospace; "
            f"font-size: 12px; }}"
        )
        rl.addWidget(self._report, 1)
        body.addWidget(report)

        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 4)
        body.setStretchFactor(2, 2)
        body.setSizes([220, 760, 300])
        root.addWidget(body, 1)

    def _labeled(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 11px; font-weight: 700;")
        return lbl

    # ── Diff worker lifecycle ────────────────────────────────────────────

    def _start_diff(self):
        self._thread = QThread(self)
        self._worker = _DiffWorker(
            self._old_pdf, self._new_pdf, ai_client=self._ai, describe=self._describe,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_diff_finished)
        self._worker.error.connect(self._on_diff_error)
        self._thread.start()

    def _on_progress(self, current: int, total: int, msg: str):
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._status.setText(msg)

    def _on_diff_finished(self, result: SetDiffResult):
        self._result = result
        try:
            self._old_doc = fitz.open(self._old_pdf)
            self._new_doc = fitz.open(self._new_pdf)
        except Exception as exc:
            self._on_diff_error(f"Failed to open PDFs for preview: {exc}")
            return
        self._populate(result)
        self._status.setText(result.report_summary())
        self._rerun_btn.setEnabled(any(p.status != "unchanged" for p in result.pairs))
        self._cleanup_worker()

    def _on_diff_error(self, msg: str):
        QMessageBox.warning(self, "Diff failed", msg)
        self._cleanup_worker()

    def _cleanup_worker(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._thread = None
        self._worker = None

    # ── Population & selection ───────────────────────────────────────────

    _STATUS_ICON = {
        "modified": "✏",
        "added": "✚",
        "removed": "✗",
        "structural": "⚠",
        "unchanged": "·",
    }

    def _populate(self, result: SetDiffResult):
        self._sheet_list.clear()
        # Sort: changed first, then unchanged at the bottom.
        ordered = sorted(
            result.pairs,
            key=lambda p: (p.status == "unchanged", p.sheet_id),
        )
        for pair in ordered:
            icon = self._STATUS_ICON.get(pair.status, "·")
            extra = f" ({len(pair.clusters)})" if pair.clusters else ""
            item = QListWidgetItem(f"{icon} {pair.sheet_id}{extra}")
            item.setData(Qt.ItemDataRole.UserRole, pair)
            color = {
                "added": EMERALD,
                "removed": "#EF4444",
                "modified": "#F59E0B",
                "structural": INDIGO,
                "unchanged": TEXT_3,
            }.get(pair.status, TEXT_1)
            item.setForeground(QColor(color))
            self._sheet_list.addItem(item)

        # Auto-select the first non-unchanged item.
        for i in range(self._sheet_list.count()):
            pair: PageDiff = self._sheet_list.item(i).data(Qt.ItemDataRole.UserRole)
            if pair.status != "unchanged":
                self._sheet_list.setCurrentRow(i)
                return
        if self._sheet_list.count():
            self._sheet_list.setCurrentRow(0)

    def _on_sheet_selected(self):
        items = self._sheet_list.selectedItems()
        if not items or self._result is None:
            return
        pair: PageDiff = items[0].data(Qt.ItemDataRole.UserRole)
        self._old_view.show_page(self._old_doc, pair.old_page)
        self._new_view.show_page(self._new_doc, pair.new_page)

        # Color overlays.
        if pair.status == "added":
            self._new_view.overlay_clusters(
                [DiffCluster(pdf_rect=fitz.Rect(0, 0, 1e6, 1e6), pixel_count=0)],
                _GREEN,
            )
        elif pair.status == "removed":
            self._old_view.overlay_clusters(
                [DiffCluster(pdf_rect=fitz.Rect(0, 0, 1e6, 1e6), pixel_count=0)],
                _RED,
            )
        elif pair.status == "modified":
            self._old_view.overlay_clusters(pair.clusters, _AMBER)
            self._new_view.overlay_clusters(pair.clusters, _AMBER)
        elif pair.status == "structural":
            self._old_view.overlay_clusters(
                [DiffCluster(pdf_rect=fitz.Rect(0, 0, 1e6, 1e6), pixel_count=0)],
                _AMBER,
            )

        # Report panel
        self._report.setPlainText(self._format_report(pair))

    def _format_report(self, pair: PageDiff) -> str:
        lines = [f"Sheet: {pair.sheet_id}",
                 f"Status: {pair.status}",
                 f"Old page: {pair.old_page or '—'}",
                 f"New page: {pair.new_page or '—'}",
                 ""]
        if pair.status in ("added", "removed", "structural"):
            lines.append({
                "added": "This sheet is new — no equivalent in the old set.",
                "removed": "This sheet was deleted from the new set.",
                "structural": "Could not align the pages reliably (large rotation, "
                              "scale, or content shift). Treated as fully changed.",
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

    # ── Re-extract ───────────────────────────────────────────────────────

    def _on_rerun_clicked(self):
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
        if ok == QMessageBox.StandardButton.Yes:
            self.rerun_requested.emit(self._result)

    # ── Lifecycle ────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.cancel()
        self._cleanup_worker()
        if self._old_doc is not None:
            self._old_doc.close()
            self._old_doc = None
        if self._new_doc is not None:
            self._new_doc.close()
            self._new_doc = None
        super().closeEvent(event)
