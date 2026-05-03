"""Toast notifications + Toaster singleton.

Each toast is a frameless overlay anchored to the top-right of the
``QApplication.activeWindow()``. Toasts auto-dismiss via
``Animator.fade_out`` after their duration. The Toaster keeps a queue
capped at 4 visible toasts; older ones are dismissed early to make room.
"""
from __future__ import annotations

from collections import deque
from typing import Literal

from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import Animator, tokens

ToastVariant = Literal["info", "success", "warning", "danger"]

_VARIANT_TO_COLOR_KEY: dict[ToastVariant, tuple[str, ...]] = {
    "info": ("info",),
    "success": ("accent", "default"),
    "warning": ("warning",),
    "danger": ("danger",),
}


def _color_for(variant: ToastVariant) -> str:
    keys = _VARIANT_TO_COLOR_KEY[variant]
    node = tokens["color"]
    for k in keys:
        node = node[k]
    return node  # type: ignore[return-value]


class Toast(QFrame):
    """Single toast widget. Owned + positioned by the Toaster."""

    def __init__(
        self,
        message: str,
        variant: ToastVariant = "info",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._variant = variant
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        stripe_color = _color_for(variant)
        self.setStyleSheet(
            f"#toast {{"
            f"  background-color: {tokens['color']['bg']['surface']['raised']};"
            f"  border: 1px solid {tokens['color']['border']['default']};"
            f"  border-left: 4px solid {stripe_color};"
            f"  border-radius: {tokens['radius']['md']}px;"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens["space"][3], tokens["space"][2],
            tokens["space"][3], tokens["space"][2],
        )
        layout.setSpacing(tokens["space"][3])

        self._label = QLabel(message, self)
        self._label.setProperty("textSize", "body")
        layout.addWidget(self._label, 1)

        self._dismiss_btn = QPushButton("×", self)
        self._dismiss_btn.setFlat(True)
        self._dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {tokens['color']['text']['secondary']}; font-size: 18px; "
            f"padding: 0 4px; }}"
        )
        self._dismiss_btn.clicked.connect(lambda: Toaster.dismiss(self))
        layout.addWidget(self._dismiss_btn, 0)

        self.setMinimumWidth(280)
        self.setMaximumWidth(420)


class _ToasterImpl:
    """Singleton manager for active toasts."""

    MAX_VISIBLE = 4

    def __init__(self) -> None:
        self._toasts: deque[Toast] = deque()
        self._timers: dict[int, QTimer] = {}

    def show(
        self,
        message: str,
        variant: ToastVariant = "info",
        duration_ms: int = 3500,
        parent: QWidget | None = None,
    ) -> Toast | None:
        host = parent or QApplication.activeWindow()
        if host is None:
            # No active window — silently no-op rather than crash.
            return None

        toast = Toast(message, variant=variant, parent=host)
        toast.adjustSize()
        self._toasts.append(toast)

        while len(self._toasts) > self.MAX_VISIBLE:
            self.dismiss(self._toasts[0])

        self._reflow(host)
        Animator.fade_in(toast, duration_ms=200)

        timer = QTimer(toast)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.dismiss(toast))
        timer.start(max(500, duration_ms))
        self._timers[id(toast)] = timer
        return toast

    def dismiss(self, toast: Toast) -> None:
        if toast not in self._toasts:
            return
        try:
            self._toasts.remove(toast)
        except ValueError:
            pass
        timer = self._timers.pop(id(toast), None)
        if timer is not None:
            timer.stop()
        anim = Animator.fade_out(toast, duration_ms=180)
        anim.finished.connect(toast.deleteLater)
        host = QApplication.activeWindow()
        if host is not None:
            self._reflow(host)

    def visibleToasts(self) -> list[Toast]:
        return list(self._toasts)

    def _reflow(self, host: QWidget) -> None:
        margin = tokens["space"][4]
        gap = tokens["space"][2]
        host_rect = host.rect()
        y = margin
        for t in self._toasts:
            t.adjustSize()
            x = host_rect.right() - t.width() - margin
            t.move(QPoint(x, y))
            t.raise_()
            y += t.height() + gap


# Module-level singleton — exposed under both names so callers can write
# ``Toaster.show(...)`` per the API contract while internals access the
# instance.
Toaster = _ToasterImpl()


__all__ = ["Toast", "Toaster", "ToastVariant"]
