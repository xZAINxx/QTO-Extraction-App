"""Embedded PDF viewer — QGraphicsView over PyMuPDF pixmaps.

Phase-4 deliverable. Supports:
    - Loading a PDF and lazy page rendering (cached pixmaps).
    - Ctrl + scroll-wheel zoom (anchored to cursor).
    - Click-drag panning.
    - Programmatic ``go_to_page(n)``.
    - ``highlight_region(rect)`` to flash a coloured overlay on the
      current page (used when the user clicks a row in ``ResultsTable``).
    - ``capture_region()`` interaction mode for drawing a bounding
      box and emitting the cropped pixmap (consumed by Pattern Search).

The rendering pixmap is intentionally produced at a fixed DPI; the
``QGraphicsView`` transform handles smooth zoom without re-rasterising.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QEvent, QPoint
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QColor, QPen, QBrush, QWheelEvent,
    QMouseEvent, QKeyEvent,
)
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy,
)

from ui.theme import (
    SURFACE_1, SURFACE_2, SURFACE_3, BORDER_HEX, TEXT_1, TEXT_2,
    INDIGO, EMERALD,
)


_LOG = logging.getLogger(__name__)
_RENDER_DPI = 144  # 2x for crisp text without ballooning memory


@dataclass(frozen=True)
class CapturedRegion:
    page_num: int          # 1-indexed
    pdf_rect: fitz.Rect    # in mediabox/pdf coordinates
    pixmap: QPixmap        # cropped raster (for vision/template-match)


class _PdfScene(QGraphicsScene):
    """Scene that owns one cached pixmap item per page rendered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundBrush(QBrush(QColor(SURFACE_1)))
        self._page_item: Optional[QGraphicsPixmapItem] = None
        self._highlight_item: Optional[QGraphicsRectItem] = None
        self._marquee_item: Optional[QGraphicsRectItem] = None

    def set_page_pixmap(self, pix: QPixmap):
        if self._page_item is None:
            self._page_item = QGraphicsPixmapItem(pix)
            self._page_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.addItem(self._page_item)
        else:
            self._page_item.setPixmap(pix)
        self.setSceneRect(QRectF(pix.rect()))
        self.clear_highlight()
        self.clear_marquee()

    # ── Highlight (one transient rect on the active page) ────────────────

    def clear_highlight(self):
        if self._highlight_item is not None:
            self.removeItem(self._highlight_item)
            self._highlight_item = None

    def show_highlight(self, scene_rect: QRectF):
        self.clear_highlight()
        pen = QPen(QColor(EMERALD))
        pen.setWidthF(3.0)
        pen.setCosmetic(True)
        brush = QBrush(QColor(16, 185, 129, 50))   # emerald @ ~20% alpha
        self._highlight_item = self.addRect(scene_rect, pen, brush)
        if self._highlight_item is not None:
            self._highlight_item.setZValue(10)

    # ── Marquee (live drag rectangle for capture mode) ───────────────────

    def begin_marquee(self, origin: QPointF):
        self.clear_marquee()
        pen = QPen(QColor(INDIGO))
        pen.setWidthF(2.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        brush = QBrush(QColor(99, 102, 241, 40))
        self._marquee_item = self.addRect(QRectF(origin, origin), pen, brush)
        if self._marquee_item is not None:
            self._marquee_item.setZValue(20)

    def update_marquee(self, rect: QRectF):
        if self._marquee_item is not None:
            self._marquee_item.setRect(rect.normalized())

    def clear_marquee(self):
        if self._marquee_item is not None:
            self.removeItem(self._marquee_item)
            self._marquee_item = None


class PDFGraphicsView(QGraphicsView):
    """Inner ``QGraphicsView`` with zoom + pan + capture-box behaviour."""

    region_captured = pyqtSignal(QRectF)   # scene-coordinate rect when capture done
    page_clicked = pyqtSignal(QPointF)     # scene point when not in capture mode

    _MIN_SCALE = 0.15
    _MAX_SCALE = 8.0
    _ZOOM_STEP = 1.15

    def __init__(self, scene: _PdfScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(
            f"QGraphicsView {{ background: {SURFACE_1}; border: none; }}"
        )
        self._scene = scene
        self._scale = 1.0
        self._capture_mode = False
        self._marquee_origin: Optional[QPointF] = None
        # Track movement without a held button so detail-callout hover
        # detection fires as the cursor passes over a callout.
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    # ── Capture mode ─────────────────────────────────────────────────────

    def set_capture_mode(self, enabled: bool):
        self._capture_mode = enabled
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag
            if enabled
            else QGraphicsView.DragMode.ScrollHandDrag
        )
        self.setCursor(
            Qt.CursorShape.CrossCursor
            if enabled
            else Qt.CursorShape.ArrowCursor
        )

    # ── Zoom / pan ───────────────────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._zoom_by(self._ZOOM_STEP if event.angleDelta().y() > 0 else 1 / self._ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def _zoom_by(self, factor: float):
        new_scale = max(self._MIN_SCALE, min(self._MAX_SCALE, self._scale * factor))
        if abs(new_scale - self._scale) < 1e-6:
            return
        applied = new_scale / self._scale
        self.scale(applied, applied)
        self._scale = new_scale

    def zoom_in(self):
        self._zoom_by(self._ZOOM_STEP)

    def zoom_out(self):
        self._zoom_by(1 / self._ZOOM_STEP)

    def reset_zoom(self):
        self.resetTransform()
        self._scale = 1.0

    def fit_page(self, rect: QRectF):
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._scale = self.transform().m11()

    # ── Mouse for marquee + plain click ──────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if self._capture_mode and event.button() == Qt.MouseButton.LeftButton:
            self._marquee_origin = self.mapToScene(event.pos())
            self._scene.begin_marquee(self._marquee_origin)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._capture_mode and self._marquee_origin is not None:
            cur = self.mapToScene(event.pos())
            self._scene.update_marquee(QRectF(self._marquee_origin, cur))
            event.accept()
            return
        # Forward hover-tracking to the owning PDFViewer so the detail
        # callout popup can light up under the cursor (no-op if no
        # callouts are registered for the current page).
        owner = self.parent()
        if owner is not None and hasattr(owner, "_handle_callout_hover"):
            owner._handle_callout_hover(self.mapToScene(event.pos()), event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if (
            self._capture_mode
            and event.button() == Qt.MouseButton.LeftButton
            and self._marquee_origin is not None
        ):
            cur = self.mapToScene(event.pos())
            rect = QRectF(self._marquee_origin, cur).normalized()
            self._marquee_origin = None
            if rect.width() > 6 and rect.height() > 6:
                self.region_captured.emit(rect)
            self._scene.clear_marquee()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._capture_mode:
            scene_point = self.mapToScene(event.pos())
            owner = self.parent()
            # Detail callouts get first crack — if the click hit one with
            # a known target page, swallow the event so we don't also fire
            # the row-lookup ``page_clicked`` path.
            if (
                owner is not None
                and hasattr(owner, "_handle_callout_click")
                and owner._handle_callout_click(scene_point)
            ):
                event.accept()
                return
            self.page_clicked.emit(scene_point)
        super().mouseReleaseEvent(event)


class PDFViewer(QWidget):
    """Composite widget: PDF view + thin top toolbar (page nav + zoom)."""

    region_captured = pyqtSignal(object)   # CapturedRegion
    page_changed = pyqtSignal(int)          # 1-indexed page
    region_clicked = pyqtSignal(int, tuple)  # (page_num, pdf_bbox) on plain LMB click
    detail_jump_requested = pyqtSignal(int)  # 1-indexed target page from a callout click

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("pdfViewer")
        self._doc: Optional[fitz.Document] = None
        self._pdf_path: Optional[str] = None
        self._page_num: int = 1
        self._render_zoom: float = _RENDER_DPI / 72.0
        self._pixmap_cache: dict[int, QPixmap] = {}
        self._page_rect: Optional[fitz.Rect] = None  # current page mediabox
        self._page_rotation: int = 0

        # Trace-back / zone overlay state — see "Trace-back + zone overlays"
        # section at the bottom of this module.
        self._pulse_anim = None  # type: ignore[assignment]
        self._zone_items: list[QGraphicsRectItem] = []
        self.zone_overlay_visible: bool = False

        # Detail-callout state — see "Detail callout previews" section at
        # the bottom of this module. Populated lazily by MainWindow as
        # pages scroll into view via parser.callout_detector.detect_callouts.
        self._detail_callouts: dict[int, list[tuple[QRectF, str, int]]] = {}
        self._callout_popup: Optional[QFrame] = None
        self._hovered_callout_key: Optional[tuple[int, int]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Build the view first so the toolbar's zoom buttons can reference it.
        self._scene = _PdfScene(self)
        self._view = PDFGraphicsView(self._scene, self)
        self._view.region_captured.connect(self._on_region_captured)
        self._view.page_clicked.connect(self._on_page_clicked)

        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._view, 1)

        self.setStyleSheet(
            f"#pdfViewer {{ background: {SURFACE_1}; }}"
            f"#pdfToolbar {{ background: {SURFACE_2}; border-bottom: 1px solid {BORDER_HEX}; }}"
        )

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("pdfToolbar")
        bar.setFixedHeight(36)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(6)

        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.clicked.connect(self.previous_page)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(self.next_page)

        self._page_label = QLabel("— / —")
        self._page_label.setStyleSheet(f"color: {TEXT_2}; padding: 0 8px;")

        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(28)
        zoom_in.clicked.connect(self._view.zoom_in)
        zoom_out = QPushButton("−")
        zoom_out.setFixedWidth(28)
        zoom_out.clicked.connect(self._view.zoom_out)
        zoom_fit = QPushButton("Fit")
        zoom_fit.setFixedWidth(40)
        zoom_fit.clicked.connect(self._fit_current_page)

        for w in (self._prev_btn, self._next_btn, self._page_label):
            h.addWidget(w)
        h.addStretch()
        for w in (zoom_out, zoom_fit, zoom_in):
            h.addWidget(w)
        return bar

    # ── Public API ────────────────────────────────────────────────────────

    def load(self, pdf_path: str) -> bool:
        try:
            self._doc = fitz.open(pdf_path)
        except Exception as exc:
            _LOG.error("failed to open %s: %s", pdf_path, exc)
            return False
        self._pdf_path = pdf_path
        self._pixmap_cache.clear()
        self.go_to_page(1)
        self._fit_current_page()
        return True

    @property
    def pdf_path(self) -> Optional[str]:
        return self._pdf_path

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 0

    @property
    def current_page(self) -> int:
        return self._page_num

    def go_to_page(self, page_num: int):
        if not self._doc:
            return
        page_num = max(1, min(self.page_count, page_num))
        if page_num == self._page_num and page_num in self._pixmap_cache:
            return
        self._page_num = page_num
        page = self._doc[page_num - 1]
        self._page_rect = page.mediabox
        self._page_rotation = page.rotation
        pix = self._pixmap_cache.get(page_num)
        if pix is None:
            mat = fitz.Matrix(self._render_zoom, self._render_zoom)
            raw = page.get_pixmap(matrix=mat, alpha=False)
            img = QImage(
                raw.samples, raw.width, raw.height, raw.stride, QImage.Format.Format_RGB888
            ).copy()
            pix = QPixmap.fromImage(img)
            self._pixmap_cache[page_num] = pix
        self._scene.set_page_pixmap(pix)
        self._page_label.setText(f"{page_num} / {self.page_count}")
        self._prev_btn.setEnabled(page_num > 1)
        self._next_btn.setEnabled(page_num < self.page_count)
        self.page_changed.emit(page_num)

    def previous_page(self):
        self.go_to_page(self._page_num - 1)

    def next_page(self):
        self.go_to_page(self._page_num + 1)

    def set_capture_mode(self, enabled: bool):
        self._view.set_capture_mode(enabled)

    def highlight_pdf_rect(self, page_num: int, pdf_rect: fitz.Rect):
        """Scroll to ``page_num`` and flash a highlight at ``pdf_rect``."""
        self.go_to_page(page_num)
        scene_rect = self._pdf_to_scene_rect(pdf_rect)
        self._scene.show_highlight(scene_rect)
        self._view.centerOn(scene_rect.center())

    def clear_highlight(self):
        self._scene.clear_highlight()

    # ── Coordinate helpers ───────────────────────────────────────────────

    def _pdf_to_scene_rect(self, pdf_rect: fitz.Rect) -> QRectF:
        """Map a PDF-space rect to scene coordinates (post rendering DPI)."""
        z = self._render_zoom
        # When the page is rotated, fitz already rotated the rendered pixmap
        # to "visual" orientation; pdf_rect is in mediabox space. We rotate
        # the rect to the rendered-pixmap orientation here.
        page = self._doc[self._page_num - 1] if self._doc else None
        if page is None or self._page_rect is None:
            return QRectF(pdf_rect.x0 * z, pdf_rect.y0 * z,
                          pdf_rect.width * z, pdf_rect.height * z)
        rot = self._page_rotation % 360
        w, h = self._page_rect.width, self._page_rect.height
        x0, y0, x1, y1 = pdf_rect.x0, pdf_rect.y0, pdf_rect.x1, pdf_rect.y1
        if rot == 0:
            sx0, sy0, sx1, sy1 = x0, y0, x1, y1
        elif rot == 90:
            sx0, sy0, sx1, sy1 = h - y1, x0, h - y0, x1
        elif rot == 180:
            sx0, sy0, sx1, sy1 = w - x1, h - y1, w - x0, h - y0
        elif rot == 270:
            sx0, sy0, sx1, sy1 = y0, w - x1, y1, w - x0
        else:
            sx0, sy0, sx1, sy1 = x0, y0, x1, y1
        return QRectF(sx0 * z, sy0 * z, (sx1 - sx0) * z, (sy1 - sy0) * z)

    def scene_to_pdf_rect(self, scene_rect: QRectF) -> Optional[fitz.Rect]:
        """Inverse of :meth:`_pdf_to_scene_rect`. Returns ``None`` if no doc."""
        if not self._doc or self._page_rect is None:
            return None
        z = self._render_zoom
        x0, y0 = scene_rect.x() / z, scene_rect.y() / z
        x1, y1 = x0 + scene_rect.width() / z, y0 + scene_rect.height() / z
        rot = self._page_rotation % 360
        w, h = self._page_rect.width, self._page_rect.height
        if rot == 0:
            px0, py0, px1, py1 = x0, y0, x1, y1
        elif rot == 90:
            px0, py0, px1, py1 = y0, h - x1, y1, h - x0
        elif rot == 180:
            px0, py0, px1, py1 = w - x1, h - y1, w - x0, h - y0
        elif rot == 270:
            px0, py0, px1, py1 = w - y1, x0, w - y0, x1
        else:
            px0, py0, px1, py1 = x0, y0, x1, y1
        return fitz.Rect(px0, py0, px1, py1)

    # ── Internal slots ───────────────────────────────────────────────────

    def _fit_current_page(self):
        if self._doc and self._page_num in self._pixmap_cache:
            self._view.fit_page(self._scene.sceneRect())

    def _on_region_captured(self, scene_rect: QRectF):
        if not self._doc:
            return
        page = self._doc[self._page_num - 1]
        pdf_rect = self.scene_to_pdf_rect(scene_rect)
        if pdf_rect is None:
            return
        # Render only the captured region at high DPI for downstream CV.
        clip = pdf_rect & self._page_rect
        if clip.is_empty:
            return
        mat = fitz.Matrix(self._render_zoom, self._render_zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        img = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        ).copy()
        qpix = QPixmap.fromImage(img)
        self.region_captured.emit(
            CapturedRegion(page_num=self._page_num, pdf_rect=clip, pixmap=qpix)
        )

    # ── Trace-back + zone overlays ────────────────────────────────────────
    #
    # Surgical additions for Wave 4 commit 6 (dapper-pebble plan section
    # "5. Phase 1 features", row #1: trace-back overlay; plus the addendum
    # that surfaces ``parser.zone_segmenter`` results visibly on the canvas
    # — Caddie-style zone mapping). Everything below this banner was added
    # by the trace-back commit; nothing above it knows these methods exist.

    _PULSE_DURATION_MS = 400
    _PULSE_LOOPS = 3            # 3 cycles × 400ms ≈ 1200ms total
    _PULSE_ALPHA = 89           # ~35% of 255 — confirmed-yellow at 35% alpha
    _CLICK_BBOX_PDF_UNITS = 30  # 30×30 pdf-unit click bbox → row lookup

    def _on_page_clicked(self, scene_point: QPointF) -> None:
        """Translate a non-capture-mode left click into a small PDF bbox."""
        if not self._doc or self._page_rect is None:
            return
        half = self._CLICK_BBOX_PDF_UNITS * self._render_zoom / 2.0
        scene_rect = QRectF(
            scene_point.x() - half,
            scene_point.y() - half,
            half * 2,
            half * 2,
        )
        pdf_rect = self.scene_to_pdf_rect(scene_rect)
        if pdf_rect is None:
            return
        bbox = (
            float(pdf_rect.x0),
            float(pdf_rect.y0),
            float(pdf_rect.x1),
            float(pdf_rect.y1),
        )
        self.region_clicked.emit(self._page_num, bbox)

    def pulse_highlight(
        self,
        page_num: int,
        pdf_rect: tuple[float, float, float, float],
    ) -> None:
        """Scroll to ``page_num`` and pulse a yellow highlight at ``pdf_rect``.

        The fill is ``confirmed-yellow`` at 35% alpha — picked over brand
        amber because amber is hard to see on the white-mode PDF canvas.
        Three opacity cycles over ~1200ms then fade away (the highlight
        item is removed when the animation finishes).
        """
        if not self._doc:
            return
        self.go_to_page(page_num)
        if self._page_rect is None:
            return
        rect = fitz.Rect(*pdf_rect)
        scene_rect = self._pdf_to_scene_rect(rect)

        # Stop any prior pulse before adding the new highlight item, so the
        # previous animation doesn't tear down the rect we just added.
        if self._pulse_anim is not None:
            try:
                self._pulse_anim.stop()
            except RuntimeError:
                pass
            self._pulse_anim = None

        self._scene.clear_highlight()
        from ui.theme import tokens  # local import — theme package only loaded when used
        color = QColor(tokens["color"]["confirmed-yellow"])
        color.setAlpha(self._PULSE_ALPHA)
        pen = QPen(QColor(tokens["color"]["confirmed-yellow"]))
        pen.setWidthF(2.0)
        pen.setCosmetic(True)
        item = self._scene.addRect(scene_rect, pen, QBrush(color))
        if item is None:
            return
        item.setZValue(15)
        self._scene._highlight_item = item  # so clear_highlight removes it

        from PyQt6.QtCore import QPropertyAnimation, QAbstractAnimation
        anim = QPropertyAnimation(self)  # parented to the viewer for lifecycle
        anim.setTargetObject(self)
        anim.setPropertyName(b"_pulse_alpha_property")
        anim.setDuration(self._PULSE_DURATION_MS)
        anim.setKeyValueAt(0.0, 0.4)
        anim.setKeyValueAt(0.5, 0.7)
        anim.setKeyValueAt(1.0, 0.4)
        anim.setLoopCount(self._PULSE_LOOPS)

        # Drive item-level opacity each frame via a captured closure — avoids
        # adding a Qt property to PDFViewer just for this animation.
        def _step(value: float, _it=item) -> None:
            try:
                _it.setOpacity(float(value))
            except RuntimeError:
                pass

        anim.valueChanged.connect(lambda v: _step(v))
        anim.finished.connect(self._scene.clear_highlight)
        anim.finished.connect(lambda: setattr(self, "_pulse_anim", None))
        self._pulse_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._view.centerOn(scene_rect.center())

    def show_zone_overlay(self, page_num: int, zones) -> None:
        """Draw faint colored rectangles for each classified zone on the page.

        Surfaces the existing ``parser.zone_segmenter`` output on the canvas
        — title block (grey), legends (mep-blue), schedules (approved-green),
        plan_bodies (confirmed-yellow @ 8%), notes (text-secondary). Each rect
        gets a 1px coloured border at 60% alpha so the colour reads even when
        the fill is barely visible.
        """
        self.hide_zone_overlay()
        if not self._doc or self._page_rect is None:
            return
        if page_num != self._page_num:
            self.go_to_page(page_num)
        if self._page_rect is None:
            return

        from ui.theme import tokens
        # (zone_attr, color_hex, fill_alpha_pct)
        spec = [
            ("title_block", tokens["color"]["text"]["secondary"], 12),
            ("legends", tokens["color"]["mep-blue"], 12),
            ("schedules", tokens["color"]["approved-green"], 12),
            ("plan_bodies", tokens["color"]["confirmed-yellow"], 8),
            ("notes", tokens["color"]["text"]["secondary"], 12),
        ]
        for attr, hex_color, fill_pct in spec:
            value = getattr(zones, attr, None)
            if value is None:
                continue
            zone_list = value if isinstance(value, list) else [value]
            for z in zone_list:
                if z is None or getattr(z, "rect", None) is None:
                    continue
                self._add_zone_rect(z.rect, hex_color, fill_pct)
        self.zone_overlay_visible = True

    def hide_zone_overlay(self) -> None:
        """Remove every zone rectangle currently drawn on the scene."""
        for item in self._zone_items:
            try:
                self._scene.removeItem(item)
            except RuntimeError:
                pass
        self._zone_items = []
        self.zone_overlay_visible = False

    def _add_zone_rect(self, pdf_rect, hex_color: str, fill_pct: int) -> None:
        scene_rect = self._pdf_to_scene_rect(pdf_rect)
        fill = QColor(hex_color)
        fill.setAlpha(int(round(255 * fill_pct / 100.0)))
        border = QColor(hex_color)
        border.setAlpha(int(round(255 * 0.60)))
        pen = QPen(border)
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
        item = self._scene.addRect(scene_rect, pen, QBrush(fill))
        if item is None:
            return
        item.setZValue(5)  # below pulse highlight (15) and marquee (20)
        self._zone_items.append(item)

    # ── Detail callout previews ──────────────────────────────────────────
    #
    # Wave 6 commit 12 (dapper-pebble plan section "6. Phase 2 features",
    # row #12). Architectural drawings reference details via callouts like
    # ``4/A-501`` (Detail 4 on Sheet A-501). MainWindow runs
    # ``parser.callout_detector.detect_callouts`` on the active page and
    # registers the results here via ``set_detail_callouts``. Hover →
    # tooltip popup; click → ``detail_jump_requested`` signal.

    def set_detail_callouts(
        self,
        page_num: int,
        callouts: list[tuple[QRectF, str, int]],
    ) -> None:
        """Register clickable callouts for ``page_num``.

        Each entry is ``(scene_rect, callout_text, target_page_num)``;
        ``target_page_num == 0`` means the destination is unknown (no
        sheet match) — hover still works but click is a no-op.
        """
        self._detail_callouts[page_num] = list(callouts)

    def _handle_callout_hover(self, scene_point: QPointF, global_pos) -> None:
        hit = self._callout_hit_test(scene_point)
        if hit is None:
            self._hide_callout_popup()
            return
        idx, (_rect, text, target) = hit
        key = (self._page_num, idx)
        if key == self._hovered_callout_key:
            return
        self._hovered_callout_key = key
        self._show_callout_popup(text, target, global_pos)

    def _handle_callout_click(self, scene_point: QPointF) -> bool:
        hit = self._callout_hit_test(scene_point)
        if hit is None:
            return False
        _idx, (_rect, _text, target) = hit
        if target > 0:
            self._hide_callout_popup()
            self.detail_jump_requested.emit(int(target))
            return True
        return False

    def _callout_hit_test(self, scene_point: QPointF):
        callouts = self._detail_callouts.get(self._page_num)
        if not callouts:
            return None
        for idx, entry in enumerate(callouts):
            rect = entry[0]
            if rect.contains(scene_point):
                return idx, entry
        return None

    def _show_callout_popup(self, text: str, target_page: int, global_pos) -> None:
        if self._callout_popup is None:
            popup = QFrame(self, Qt.WindowType.ToolTip)
            popup.setObjectName("calloutPopup")
            popup.setStyleSheet(
                f"#calloutPopup {{ background: {SURFACE_2}; "
                f"border: 1px solid {BORDER_HEX}; border-radius: 6px; }} "
                f"QLabel {{ color: {TEXT_1}; padding: 4px 8px; }}"
            )
            lay = QVBoxLayout(popup)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            popup._title = QLabel("", popup)  # type: ignore[attr-defined]
            popup._title.setStyleSheet(  # type: ignore[attr-defined]
                f"font-weight: 600; color: {TEXT_1}; padding: 6px 10px 2px;"
            )
            popup._jump = QPushButton("Jump to sheet", popup)  # type: ignore[attr-defined]
            popup._jump.setEnabled(False)  # type: ignore[attr-defined]
            popup._target = 0  # type: ignore[attr-defined]
            popup._jump.clicked.connect(  # type: ignore[attr-defined]
                lambda: self._on_popup_jump_clicked()
            )
            lay.addWidget(popup._title)  # type: ignore[attr-defined]
            lay.addWidget(popup._jump)  # type: ignore[attr-defined]
            popup.setFixedWidth(180)
            self._callout_popup = popup
        self._callout_popup._title.setText(text)  # type: ignore[attr-defined]
        self._callout_popup._target = int(target_page)  # type: ignore[attr-defined]
        self._callout_popup._jump.setEnabled(target_page > 0)  # type: ignore[attr-defined]
        try:
            self._callout_popup.move(global_pos + QPoint(14, 14))
        except TypeError:
            pass
        self._callout_popup.show()

    def _on_popup_jump_clicked(self) -> None:
        if self._callout_popup is None:
            return
        target = int(getattr(self._callout_popup, "_target", 0))
        self._hide_callout_popup()
        if target > 0:
            self.detail_jump_requested.emit(target)

    def _hide_callout_popup(self) -> None:
        self._hovered_callout_key = None
        if self._callout_popup is not None and self._callout_popup.isVisible():
            self._callout_popup.hide()
