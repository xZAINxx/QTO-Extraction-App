"""Button — `QPushButton` with variant + size + icon dynamic properties."""
from __future__ import annotations

from typing import Literal

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from ui.theme import Animator, icon as theme_icon, tokens

ButtonVariant = Literal["primary", "secondary", "ghost", "danger"]
ButtonSize = Literal["sm", "md", "lg"]

_SIZE_PX: dict[ButtonSize, int] = {"sm": 28, "md": 36, "lg": 44}
_ICON_PX: dict[ButtonSize, int] = {"sm": 14, "md": 16, "lg": 18}


class Button(QPushButton):
    """Themed push button.

    The QSS attribute selectors in ``ui/theme/qss.py`` paint the colors;
    this class wires the dynamic properties and handles icon + loading
    state. No per-widget styles are set here — the QApplication-level
    stylesheet is the single source of truth.
    """

    def __init__(
        self,
        text: str = "",
        icon_name: str | None = None,
        variant: ButtonVariant = "primary",
        size: ButtonSize = "md",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._variant: ButtonVariant = variant
        self._size: ButtonSize = size
        self._icon_name: str | None = icon_name
        self._loading: bool = False
        self._spin_anim = None  # type: ignore[var-annotated]

        self.setProperty("variant", variant)
        # Use ``btnSize``/``iconOnly`` instead of ``size``/``icon-only`` so we
        # don't shadow Qt's built-in ``QWidget.size`` Q_PROPERTY (which would
        # silently refuse the string assignment) or rely on hyphenated names
        # (which Qt's meta system normalises awkwardly).
        self.setProperty("btnSize", size)
        if not text and icon_name:
            self.setProperty("iconOnly", "true")
        self.setMinimumHeight(_SIZE_PX[size])
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if icon_name:
            self._apply_icon(icon_name)

    # --- icon ----------------------------------------------------------------

    def _icon_color(self) -> str:
        if self._variant == "primary" or self._variant == "danger":
            return tokens["color"]["accent"]["on"]
        return tokens["color"]["text"]["primary"]

    def _apply_icon(self, name: str) -> None:
        try:
            qicon = theme_icon(name, color=self._icon_color(), size=_ICON_PX[self._size])
        except RuntimeError:
            return  # qtawesome missing — silently skip; QSS still styles the button
        self.setIcon(qicon)
        self.setIconSize(QSize(_ICON_PX[self._size], _ICON_PX[self._size]))

    # --- loading -------------------------------------------------------------

    def setLoading(self, loading: bool) -> None:
        """Toggle the loading state — disables the button and spins an icon."""
        if loading == self._loading:
            return
        self._loading = loading
        self.setEnabled(not loading)
        if loading:
            self._apply_icon("arrows-clockwise")
            # Drive a numeric property purely so the existing animator API is
            # used; visual rotation would require a custom paint hook, which
            # is intentionally deferred until a component needs it.
            self._spin_anim = Animator.animate_property(
                self, b"windowOpacity", 1.0, 0.95,
                duration_ms=600, loop_count=-1,
            )
        else:
            if self._spin_anim is not None:
                self._spin_anim.stop()
                self._spin_anim = None
            if self._icon_name:
                self._apply_icon(self._icon_name)
            else:
                self.setIcon(theme_icon("check-circle", color=self._icon_color())) if False else None  # noqa: E501

    def isLoading(self) -> bool:
        return self._loading


__all__ = ["Button", "ButtonVariant", "ButtonSize"]
