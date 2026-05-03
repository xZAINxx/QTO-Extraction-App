"""Skeleton — loading placeholder with a shimmering gradient sweep.

QSS gradients do not animate, so we paint manually:
    * The widget exposes a ``shimmerOffset`` Qt-property (float, 0.0–1.5)
      that ``Animator`` drives in a -1 (infinite) loop.
    * ``paintEvent`` builds a horizontal ``QLinearGradient`` whose three
      stops translate by that offset, producing a smooth left-to-right
      shimmer over the surface-2 base color.
"""
from __future__ import annotations

from typing import Literal

from PyQt6.QtCore import QSize, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from ui.theme import Animator, tokens

SkeletonShape = Literal["line", "block", "row"]

_SHAPE_DIM: dict[SkeletonShape, tuple[int | None, int | None]] = {
    # (width, height) — None means "stretch"
    "line": (None, 20),   # space.5 = 20
    "block": (80, 80),
    "row": (None, 24),
}


class Skeleton(QWidget):
    """Shimmering surface used while data is loading."""

    def __init__(self, shape: SkeletonShape = "line", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shape: SkeletonShape = shape
        self._offset: float = 0.0
        w, h = _SHAPE_DIM[shape]
        if w is not None:
            self.setFixedWidth(w)
        if h is not None:
            self.setFixedHeight(h)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"background-color: {tokens['color']['bg']['surface']['2']};"
            f"border-radius: {tokens['radius']['md']}px;"
        )
        self._animation = Animator.animate_property(
            self, b"shimmerOffset", 0.0, 1.5, duration_ms=1500, loop_count=-1,
        )

    # --- Qt property for the animator ---------------------------------------

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    shimmerOffset = pyqtProperty(float, fget=_get_offset, fset=_set_offset)

    def shape(self) -> SkeletonShape:
        return self._shape

    def sizeHint(self) -> QSize:
        w, h = _SHAPE_DIM[self._shape]
        return QSize(w if w is not None else 200, h if h is not None else 20)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0.0, 0.0, float(self.width()), 0.0)
        base = QColor(tokens["color"]["bg"]["surface"]["2"])
        highlight = QColor(tokens["color"]["bg"]["surface"]["3"])
        # Shimmer band travels 0..1.5 so it fully crosses the widget edge.
        center = max(0.0, min(1.0, self._offset - 0.25))
        left = max(0.0, center - 0.2)
        right = min(1.0, center + 0.2)
        gradient.setColorAt(0.0, base)
        gradient.setColorAt(left, base)
        gradient.setColorAt(center, highlight)
        gradient.setColorAt(right, base)
        gradient.setColorAt(1.0, base)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(
            self.rect(), tokens["radius"]["md"], tokens["radius"]["md"]
        )
        painter.end()


__all__ = ["Skeleton", "SkeletonShape"]
