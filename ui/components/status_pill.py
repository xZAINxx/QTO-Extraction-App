"""StatusPill — confidence percentage + next-action label, color-coded."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from ui.components.pill import Pill, PillVariant


def _classify(confidence: float) -> tuple[PillVariant, str, str]:
    """Return (variant, label, action_token) for the given confidence."""
    if confidence >= 0.9:
        return "success", "Confirm", "confirm"
    if confidence >= 0.6:
        return "warning", "Review", "review"
    return "danger", "Re-extract", "re-extract"


class StatusPill(QWidget):
    """Composite pill that bundles confidence percent with the next action.

    Click anywhere on the widget to emit ``actionRequested`` with one of
    ``"confirm"`` / ``"review"`` / ``"re-extract"``. The DataTable wires
    this signal to the row-action handler in commit 5.
    """

    actionRequested = pyqtSignal(str)

    def __init__(self, confidence: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._confidence: float = confidence
        self._action_token: str = "confirm"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._inner = Pill("", variant="neutral", parent=self)
        layout.addWidget(self._inner)
        layout.addStretch(1)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    # --- public API ----------------------------------------------------------

    def confidence(self) -> float:
        return self._confidence

    def setConfidence(self, confidence: float) -> None:
        self._confidence = confidence
        self._refresh()

    def actionToken(self) -> str:
        return self._action_token

    def innerPill(self) -> Pill:
        return self._inner

    # --- internals -----------------------------------------------------------

    def _refresh(self) -> None:
        variant, label, token = _classify(self._confidence)
        percent = max(0, min(100, int(round(self._confidence * 100))))
        self._inner.setVariant(variant)
        self._inner.setText(f"{percent}% · {label}")
        self._action_token = token

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.actionRequested.emit(self._action_token)
        super().mousePressEvent(event)


__all__ = ["StatusPill"]
