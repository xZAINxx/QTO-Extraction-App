"""Pill / Badge — small rounded label with optional leading dot."""
from __future__ import annotations

from typing import Literal

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QPaintEvent, QPainter
from PyQt6.QtWidgets import QLabel, QWidget

from ui.theme import tokens

PillVariant = Literal["info", "success", "warning", "danger", "neutral"]

_DOT_DIAMETER = 6
_DOT_OFFSET_X = 8


class Pill(QLabel):
    """Inline status / category badge.

    Padding + colors come from the QSS rule for ``QLabel#pill[variant="..."]``
    in ``ui/theme/qss.py``. The optional dot is painted manually because
    QSS does not support a leading bullet that adapts to the active
    accent color.
    """

    def __init__(
        self,
        text: str = "",
        variant: PillVariant = "neutral",
        with_dot: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("pill")
        self._variant: PillVariant = variant
        self._with_dot: bool = with_dot
        self.setProperty("variant", variant)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if with_dot:
            # Reserve space for the dot inside the QLabel; QSS padding is
            # uniform so we add an indent via leading whitespace which the
            # paintEvent overdraws with the dot circle.
            self.setText(f"   {text}")
            self.setContentsMargins(_DOT_OFFSET_X + _DOT_DIAMETER, 0, 0, 0)

    def variant(self) -> PillVariant:
        return self._variant

    def setVariant(self, variant: PillVariant) -> None:
        self._variant = variant
        self.setProperty("variant", variant)
        self.style().unpolish(self)
        self.style().polish(self)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        # Pills are visually denser than default QLabels.
        return QSize(hint.width(), max(hint.height(), 22))

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._with_dot:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot_color = QColor(tokens["color"]["accent"]["default"])
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        cy = self.height() // 2
        painter.drawEllipse(
            _DOT_OFFSET_X, cy - _DOT_DIAMETER // 2, _DOT_DIAMETER, _DOT_DIAMETER
        )
        painter.end()


__all__ = ["Pill", "PillVariant"]
