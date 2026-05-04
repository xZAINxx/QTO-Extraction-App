"""qtawesome wrapper providing a stable ``icon(name)`` API.

Why wrap qtawesome:
    * The QTO codebase calls ``icon("upload")`` everywhere — short, semantic,
      vendor-neutral. Switching from Phosphor to MDI (or any other pack) is
      a one-table edit here, not a sweeping refactor.
    * Phosphor is the primary pack (line weight 1.5, the cleanest match for
      the SaaS aesthetic the redesign targets). MDI is the fallback for the
      few glyphs Phosphor lacks; the loader auto-promotes the fallback at
      lookup time so call sites stay identical.
    * qtawesome itself is an optional dependency at import time. If the
      package is missing, this module still imports cleanly — calling
      ``icon()`` raises a clear error instead of crashing the whole UI on
      startup.
"""
from __future__ import annotations

from typing import Any

try:
    import qtawesome as _qta  # type: ignore[import-untyped]
    _QTA_AVAILABLE = True
    _QTA_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover — exercised when qtawesome is missing.
    _qta = None  # type: ignore[assignment]
    _QTA_AVAILABLE = False
    _QTA_IMPORT_ERROR = exc


# 30 Phosphor icon names from the redesign plan. Keys are the semantic names
# the rest of the codebase uses; values are ``(phosphor, mdi-fallback)``.
_ICON_MAP: dict[str, tuple[str, str]] = {
    "upload":           ("ph.upload",                "mdi6.upload"),
    "play":             ("ph.play",                  "mdi6.play"),
    "stop-circle":      ("ph.stop-circle",           "mdi6.stop-circle"),
    "pause":            ("ph.pause",                 "mdi6.pause"),
    "download-simple":  ("ph.download-simple",       "mdi6.download"),
    "magnifying-glass": ("ph.magnifying-glass",      "mdi6.magnify"),
    "funnel":           ("ph.funnel",                "mdi6.filter-variant"),
    "eye":              ("ph.eye",                   "mdi6.eye-outline"),
    "check-circle":     ("ph.check-circle",          "mdi6.check-circle-outline"),
    "warning":          ("ph.warning",               "mdi6.alert-outline"),
    "x-circle":         ("ph.x-circle",              "mdi6.close-circle-outline"),
    "arrows-clockwise": ("ph.arrows-clockwise",      "mdi6.refresh"),
    "caret-left":       ("ph.caret-left",            "mdi6.chevron-left"),
    "caret-right":      ("ph.caret-right",           "mdi6.chevron-right"),
    "caret-down":       ("ph.caret-down",            "mdi6.chevron-down"),
    "dots-three":       ("ph.dots-three",            "mdi6.dots-horizontal"),
    "command":          ("ph.command",               "mdi6.apple-keyboard-command"),
    "chat-circle":      ("ph.chat-circle",           "mdi6.message-outline"),
    "gear":             ("ph.gear",                  "mdi6.cog-outline"),
    "sun":              ("ph.sun",                   "mdi6.weather-sunny"),
    "moon":             ("ph.moon",                  "mdi6.weather-night"),
    "corners-out":      ("ph.corners-out",           "mdi6.fullscreen"),
    "frame-corners":    ("ph.frame-corners",         "mdi6.crop-free"),
    "git-diff":         ("ph.git-diff",              "mdi6.source-branch"),
    "compass-tool":     ("ph.compass-tool",          "mdi6.compass-outline"),
    "paint-brush":      ("ph.paint-brush",           "mdi6.brush"),
    "tag":              ("ph.tag",                   "mdi6.tag-outline"),
    "lightbulb":        ("ph.lightbulb",             "mdi6.lightbulb-on-outline"),
    "info":             ("ph.info",                  "mdi6.information-outline"),
    "floppy-disk":      ("ph.floppy-disk",           "mdi6.content-save-outline"),
}


_DEFAULT_SIZE_PX = 18

# Cache keyed by (semantic name, color string, size). qtawesome pixmaps are
# pure functions of these inputs, so caching is safe and saves a non-trivial
# amount of allocations during table redraws.
_CACHE: dict[tuple[str, str, int], Any] = {}


def _resolve_default_color() -> str:
    # Lazy import — keeps the module importable before the active mode is set.
    from .tokens import tokens
    return tokens["color"]["text"]["primary"]


def icon(name: str, color: str | None = None, size: int | None = None) -> Any:
    """Return a ``QIcon`` for the named glyph.

    ``color`` defaults to the active theme's ``text.primary`` and is
    resolved at call time (NOT at function-def time) so theme switches
    pick up the new color naturally.

    ``size`` is the rendered pixmap edge in logical pixels; defaults to
    18 to match the QSS button paddings.
    """
    if not _QTA_AVAILABLE or _qta is None:
        raise RuntimeError(
            "qtawesome is not installed. Run `pip install -r requirements.txt`."
        ) from _QTA_IMPORT_ERROR

    if name not in _ICON_MAP:
        raise KeyError(f"Unknown icon name: {name!r}. Add it to ui/theme/icons.py _ICON_MAP.")

    color_value = color if color is not None else _resolve_default_color()
    size_value = size if size is not None else _DEFAULT_SIZE_PX
    cache_key = (name, color_value, size_value)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    primary, fallback = _ICON_MAP[name]
    candidates = (primary, fallback)
    last_error: Exception | None = None
    for spec in candidates:
        try:
            qicon = _qta.icon(spec, color=color_value)
            _CACHE[cache_key] = qicon
            return qicon
        except Exception as exc:  # qtawesome raises plain Exception for missing glyphs.
            last_error = exc
            continue

    raise RuntimeError(
        f"Could not resolve icon {name!r} via Phosphor or MDI fallback: {last_error}"
    )


def clear_cache() -> None:
    """Drop every cached QIcon. Call after switching theme mode."""
    _CACHE.clear()


__all__ = ["icon", "clear_cache"]
