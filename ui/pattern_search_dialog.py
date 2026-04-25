"""Pattern Search dialog — Phase 4 deliverable.

Workflow
--------
1. User clicks the "Pattern Search" sidebar tool → ``PatternSearchDialog``
   opens, in step 1 ("Capture sample").
2. User draws a bounding box around one example symbol in the embedded
   ``PDFViewer``. The viewer emits a :class:`CapturedRegion`; the dialog
   advances to step 2 ("Scan all sheets").
3. The background :class:`_ScanWorker` walks every page, segments it
   into ``SheetZones``, and runs :func:`cv.template_matcher.match_multiscale`
   inside each ``plan_body`` rectangle.
4. Results are presented as a checklist (sheet, count, total). On accept,
   the dialog emits one aggregated :class:`QTORow` per sheet with
   ``extraction_method='pattern_search'``.

The whole pipeline is local — zero API tokens.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import fitz
import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QSpinBox, QDoubleSpinBox, QFrame,
    QProgressBar, QStackedWidget, QWidget, QSizePolicy, QMessageBox,
    QFileDialog,
)

from cv.template_matcher import match_multiscale, TemplateMatch
from parser.zone_segmenter import segment, SheetZones
from core.qto_row import QTORow
from ui.pdf_viewer import CapturedRegion
from ui.theme import (
    SURFACE_1, SURFACE_2, SURFACE_3, BORDER_HEX, TEXT_1, TEXT_2, TEXT_3,
    INDIGO, EMERALD,
)


_LOG = logging.getLogger(__name__)
_RENDER_DPI = 150


def _qpixmap_to_ndarray(pix: QPixmap) -> np.ndarray:
    """Convert a ``QPixmap`` (RGB888) into an HxWx3 BGR uint8 array.

    OpenCV expects BGR, but match_template only uses grayscale so the
    channel order is immaterial; we keep BGR for consistency.
    """
    img = pix.toImage().convertToFormat(QImage.Format.Format_RGB888)
    width, height = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, img.bytesPerLine()))
    arr = arr[:, : width * 3].reshape((height, width, 3))
    return arr[:, :, ::-1].copy()  # RGB→BGR


def _render_zone_bgr(page: fitz.Page, zone_rect: fitz.Rect, dpi: int = _RENDER_DPI) -> tuple[np.ndarray, fitz.Matrix]:
    """Render a clipped page region to a BGR uint8 ndarray.

    Returns the array plus the ``fitz.Matrix`` used so callers can map
    pixel coordinates back to PDF space.
    """
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=zone_rect, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
    return img[:, :, ::-1].copy(), mat


@dataclass
class _SheetResult:
    page_num: int
    sheet_id: str
    matches: list[TemplateMatch] = field(default_factory=list)
    page_rect: fitz.Rect = field(default_factory=lambda: fitz.Rect(0, 0, 0, 0))


class _ScanWorker(QObject):
    progress = pyqtSignal(int, int)              # (current, total)
    sheet_done = pyqtSignal(object)              # _SheetResult
    finished = pyqtSignal(int)                   # total matches
    error = pyqtSignal(str)

    def __init__(
        self,
        pdf_path: str,
        template_bgr: np.ndarray,
        threshold: float,
        max_per_sheet: int,
    ):
        super().__init__()
        self._pdf_path = pdf_path
        self._template = template_bgr
        self._threshold = threshold
        self._max_per_sheet = max_per_sheet
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            doc = fitz.open(self._pdf_path)
        except Exception as exc:
            self.error.emit(f"Failed to open PDF: {exc}")
            return
        total_matches = 0
        try:
            n = doc.page_count
            for i in range(n):
                if self._cancel:
                    break
                page = doc[i]
                self.progress.emit(i + 1, n)
                zones = segment(page, page_num=i + 1)
                # If segmenter found no plan-body zones (rare cover sheet),
                # fall back to scanning the full mediabox so we don't miss
                # symbols on diagram-only pages.
                bodies = zones.plan_bodies or [
                    type(zones)  # placeholder type
                    .__mro__[0]   # noqa — keep mypy happy
                ]
                if not zones.plan_bodies:
                    zone_rects = [page.mediabox]
                else:
                    zone_rects = [z.rect for z in zones.plan_bodies]

                sheet_result = _SheetResult(
                    page_num=i + 1,
                    sheet_id="",
                    page_rect=page.mediabox,
                )
                for zone_rect in zone_rects:
                    if self._cancel:
                        break
                    img, mat = _render_zone_bgr(page, zone_rect)
                    matches = match_multiscale(
                        img,
                        self._template,
                        threshold=self._threshold,
                        max_matches=self._max_per_sheet,
                    )
                    # Map pixel-space matches back into PDF-space using
                    # the inverse render matrix + zone offset.
                    inv = ~mat
                    x_off, y_off = zone_rect.x0, zone_rect.y0
                    for m in matches:
                        pdf_p0 = fitz.Point(m.x0, m.y0) * inv
                        pdf_p1 = fitz.Point(m.x1, m.y1) * inv
                        sheet_result.matches.append(TemplateMatch(
                            x0=int(pdf_p0.x + x_off),
                            y0=int(pdf_p0.y + y_off),
                            x1=int(pdf_p1.x + x_off),
                            y1=int(pdf_p1.y + y_off),
                            score=m.score,
                            scale=m.scale,
                        ))
                total_matches += len(sheet_result.matches)
                self.sheet_done.emit(sheet_result)
            self.finished.emit(total_matches)
        finally:
            doc.close()


class PatternSearchDialog(QDialog):
    """Modal dialog driving the full Pattern Search workflow."""

    rows_accepted = pyqtSignal(list)   # list[QTORow]
    request_capture = pyqtSignal()      # ask the host to switch viewer to capture mode
    cancel_capture = pyqtSignal()       # ask the host to cancel capture mode

    def __init__(self, pdf_path: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Pattern Search")
        self.setModal(False)
        self.resize(520, 420)
        self._pdf_path = pdf_path
        self._template_bgr: Optional[np.ndarray] = None
        self._captured: Optional[CapturedRegion] = None
        self._results: list[_SheetResult] = []
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_ScanWorker] = None

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_capture_step())
        self._stack.addWidget(self._build_scan_step())
        self._stack.addWidget(self._build_review_step())
        root.addWidget(self._stack, 1)

    # ── Step 1: Capture sample ───────────────────────────────────────────

    def _build_capture_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        title = QLabel("Step 1 — Draw a box around the symbol you want to count")
        title.setStyleSheet(f"color: {TEXT_1}; font-weight: 600; font-size: 14px;")
        sub = QLabel(
            "The viewer is now in capture mode. Click and drag to enclose one "
            "instance of the symbol (door swing, fixture, etc.)."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {TEXT_2};")

        self._sample_preview = QLabel("(no sample captured yet)")
        self._sample_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sample_preview.setMinimumHeight(180)
        self._sample_preview.setStyleSheet(
            f"background: {SURFACE_2}; border: 1px dashed {BORDER_HEX}; color: {TEXT_3};"
        )

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._next_btn = QPushButton("Next →")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._goto_scan_step)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._next_btn)

        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addWidget(self._sample_preview, 1)
        layout.addLayout(btn_row)
        return page

    def accept_captured_region(self, region: CapturedRegion):
        """Called by the host (MainWindow) after the user finishes the marquee."""
        self._captured = region
        self._template_bgr = _qpixmap_to_ndarray(region.pixmap)
        thumb = region.pixmap.scaled(
            240, 180,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._sample_preview.setPixmap(thumb)
        self._sample_preview.setText("")
        self._next_btn.setEnabled(True)

    # ── Step 2: Scan ─────────────────────────────────────────────────────

    def _build_scan_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        title = QLabel("Step 2 — Scan every sheet")
        title.setStyleSheet(f"color: {TEXT_1}; font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        params = QHBoxLayout()
        params.setSpacing(6)

        params.addWidget(QLabel("Threshold:"))
        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.50, 0.99)
        self._threshold_spin.setSingleStep(0.02)
        self._threshold_spin.setValue(0.78)
        params.addWidget(self._threshold_spin)

        params.addSpacing(12)
        params.addWidget(QLabel("Max / sheet:"))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 500)
        self._max_spin.setValue(120)
        params.addWidget(self._max_spin)
        params.addStretch()
        layout.addLayout(params)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        layout.addWidget(self._progress_bar)

        self._status = QLabel("Ready")
        self._status.setStyleSheet(f"color: {TEXT_2};")
        layout.addWidget(self._status)
        layout.addStretch()

        btn_row = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self._goto_capture_step)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._start_scan)
        btn_row.addStretch()
        btn_row.addWidget(back_btn)
        btn_row.addWidget(self._scan_btn)
        layout.addLayout(btn_row)
        return page

    def _goto_capture_step(self):
        self._stack.setCurrentIndex(0)
        self.request_capture.emit()

    def _goto_scan_step(self):
        self._stack.setCurrentIndex(1)
        self.cancel_capture.emit()

    def _start_scan(self):
        if self._template_bgr is None or self._captured is None:
            return
        self._scan_btn.setEnabled(False)
        self._results = []
        self._progress_bar.setValue(0)
        self._status.setText("Scanning…")

        self._worker_thread = QThread(self)
        self._worker = _ScanWorker(
            self._pdf_path,
            self._template_bgr,
            self._threshold_spin.value(),
            self._max_spin.value(),
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_scan_progress)
        self._worker.sheet_done.connect(self._on_sheet_done)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker_thread.start()

    def _on_scan_progress(self, current: int, total: int):
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._status.setText(f"Scanning page {current}/{total}…")

    def _on_sheet_done(self, result: _SheetResult):
        self._results.append(result)

    def _on_scan_finished(self, total: int):
        self._cleanup_worker()
        self._scan_btn.setEnabled(True)
        self._status.setText(f"Scan complete — {total} matches across {len(self._results)} sheets.")
        self._populate_review()
        self._stack.setCurrentIndex(2)

    def _on_scan_error(self, msg: str):
        self._cleanup_worker()
        self._scan_btn.setEnabled(True)
        QMessageBox.warning(self, "Scan failed", msg)

    def _cleanup_worker(self):
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(2000)
            self._worker_thread = None
        self._worker = None

    # ── Step 3: Review ──────────────────────────────────────────────────

    def _build_review_step(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        title = QLabel("Step 3 — Review and accept counts")
        title.setStyleSheet(f"color: {TEXT_1}; font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        meta = QHBoxLayout()
        meta.addWidget(QLabel("Symbol name:"))
        self._symbol_name = QLineEdit()
        self._symbol_name.setPlaceholderText("e.g. WINDOW TYPE A1, FLOOR DRAIN, DOOR SWING")
        meta.addWidget(self._symbol_name, 1)
        meta.addWidget(QLabel("Units:"))
        self._units = QLineEdit()
        self._units.setText("EA")
        self._units.setFixedWidth(64)
        meta.addWidget(self._units)
        layout.addLayout(meta)

        self._results_list = QListWidget()
        self._results_list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE_2}; color: {TEXT_1}; "
            f"border: 1px solid {BORDER_HEX}; }}"
        )
        layout.addWidget(self._results_list, 1)

        self._total_label = QLabel("Total: 0")
        self._total_label.setStyleSheet(f"color: {EMERALD}; font-weight: 600;")
        layout.addWidget(self._total_label)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        rescan_btn = QPushButton("Re-scan")
        rescan_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        accept_btn = QPushButton("Add to Takeoff")
        accept_btn.setStyleSheet(
            f"QPushButton {{ background: {INDIGO}; color: white; "
            f"padding: 6px 14px; border-radius: 4px; font-weight: 600; }}"
        )
        accept_btn.clicked.connect(self._on_accept)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(rescan_btn)
        btn_row.addWidget(accept_btn)
        layout.addLayout(btn_row)
        return page

    def _populate_review(self):
        self._results_list.clear()
        self._results_list.itemChanged.disconnect() if self._results_list.receivers(
            self._results_list.itemChanged
        ) else None
        for sr in sorted(self._results, key=lambda r: r.page_num):
            n = len(sr.matches)
            if n == 0:
                continue
            item = QListWidgetItem(f"Page {sr.page_num} — {n} matches")
            item.setData(Qt.ItemDataRole.UserRole, sr)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._results_list.addItem(item)
        self._results_list.itemChanged.connect(self._update_total)
        self._update_total()

    def _update_total(self, _item=None):
        total = 0
        for i in range(self._results_list.count()):
            item = self._results_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                sr: _SheetResult = item.data(Qt.ItemDataRole.UserRole)
                total += len(sr.matches)
        self._total_label.setText(f"Total: {total}")

    def _on_accept(self):
        name = (self._symbol_name.text() or "PATTERN-MATCHED SYMBOL").strip().upper()
        units = (self._units.text() or "EA").strip().upper()
        rows: list[QTORow] = []
        for i in range(self._results_list.count()):
            item = self._results_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            sr: _SheetResult = item.data(Qt.ItemDataRole.UserRole)
            if not sr.matches:
                continue
            rows.append(QTORow(
                drawings=f"page-{sr.page_num}",
                details="",
                description=f"{name} — pattern-matched count",
                qty=float(len(sr.matches)),
                units=units,
                trade_division="",
                source_page=sr.page_num,
                source_sheet=f"page-{sr.page_num}",
                extraction_method="pattern_search",
                confidence=0.85,
                needs_review=True,
            ))
        if not rows:
            QMessageBox.information(self, "Nothing selected", "Tick at least one sheet to add.")
            return
        self.rows_accepted.emit(rows)
        self.accept()

    # ── Lifecycle ───────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.cancel()
        self._cleanup_worker()
        self.cancel_capture.emit()
        super().closeEvent(event)
