"""Card — `QFrame` with optional header strip + drop-shadow elevation."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ui.theme import tokens

# Map elevation level → (blur radius, alpha) tuned to match shadow tokens
# without the overhead of parsing the shadow CSS string at runtime.
_ELEVATION_TUNING: dict[int, tuple[int, int]] = {
    0: (0, 0),
    1: (8, 50),
    2: (16, 75),
    3: (24, 100),
    4: (40, 130),
}


class Card(QFrame):
    """Surface container with header / body slots."""

    def __init__(
        self,
        elevation: int = 1,
        header_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._elevation = max(0, min(4, elevation))
        self.setProperty("elevation", str(self._elevation))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header_label: QLabel | None = None
        if header_text is not None:
            self._header_label = QLabel(header_text, self)
            self._header_label.setObjectName("cardHeader")
            self._header_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            outer.addWidget(self._header_label)

        self._body = QWidget(self)
        self._body.setObjectName("cardBody")
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(
            tokens["space"][4],
            tokens["space"][4],
            tokens["space"][4],
            tokens["space"][4],
        )
        body_layout.setSpacing(tokens["space"][3])
        outer.addWidget(self._body, 1)

        self._apply_shadow()

    # --- public API ----------------------------------------------------------

    def header(self) -> QLabel | None:
        return self._header_label

    def body(self) -> QWidget:
        return self._body

    def addToBody(self, widget: QWidget) -> None:
        layout = self._body.layout()
        if layout is None:  # pragma: no cover — set in __init__
            return
        layout.addWidget(widget)

    # --- internals -----------------------------------------------------------

    def _apply_shadow(self) -> None:
        if self._elevation == 0:
            self.setGraphicsEffect(None)
            return
        blur, alpha = _ELEVATION_TUNING[self._elevation]
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(blur)
        effect.setOffset(0, max(1, self._elevation))
        shadow_color = QColor(0, 0, 0, alpha)
        effect.setColor(shadow_color)
        self.setGraphicsEffect(effect)


__all__ = ["Card"]
