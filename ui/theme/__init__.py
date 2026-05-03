"""Theme package — design tokens, QSS generator, fonts, icons, motion.

Public API:
    tokens, set_mode, get_mode, DARK, LIGHT
        Token proxy + mode switching.
    build_stylesheet(tokens)
        Render a full QSS string from the active token tree.
    load_fonts()
        Register Geist Sans + Geist Mono (no-op + warning if missing).
    icon(name, color=None, size=None)
        qtawesome wrapper with Phosphor → MDI fallback.
    Animator
        Wrapper around QPropertyAnimation that prevents overlapping animations.
    apply_theme(app, mode="dark")
        One-shot bootstrap. Sets the mode, loads fonts, applies the QSS.
        Call AGAIN (not just ``set_mode``) for runtime theme swaps because the
        QApplication's stylesheet string is generated once and would otherwise
        go stale.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .fonts import FontLoadResult, load_fonts
from .icons import clear_cache as clear_icon_cache, icon
from .motion import Animator
from .qss import build_stylesheet
from .tokens import DARK, LIGHT, get_mode, set_mode, tokens

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QApplication


def apply_theme(app: "QApplication", mode: str = "dark") -> None:
    """Bootstrap the theme on a freshly constructed ``QApplication``.

    Re-call after switching ``mode`` at runtime — both the QSS string AND
    the icon cache get refreshed so colors line up with the active palette.
    """
    set_mode(mode)
    load_fonts()
    clear_icon_cache()
    qss = build_stylesheet(tokens)
    app.setStyleSheet(qss)


__all__ = [
    "Animator",
    "DARK",
    "FontLoadResult",
    "LIGHT",
    "apply_theme",
    "build_stylesheet",
    "clear_icon_cache",
    "get_mode",
    "icon",
    "load_fonts",
    "set_mode",
    "tokens",
]
