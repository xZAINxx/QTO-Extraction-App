"""Design tokens — Zeconic QTO Tool.

This module defines the canonical token sets for both DARK and LIGHT modes
plus a `tokens` proxy object that always reflects the currently active mode.

Tri-color logic — codified here so it stays codified:
    * Brand emerald (``color.accent.*``) drives interactive chrome only:
      buttons, focus rings, links, primary CTAs. Never use it for data state
      on rows.
    * Amber (``color.warning``) drives transient AI-in-progress states and
      soft warnings only. Never use it for interactive chrome.
    * Domain semantics (``color.confirmed-yellow``, ``color.revision-pink``,
      ``color.demo-red``, ``color.approved-green``, ``color.mep-blue``,
      ``color.scope-out``) drive *data state* on rows. Never use them for
      interactive chrome.

These three palettes never cross. The plan exists because construction
estimators read color the way pilots read instruments — predictably.

Public API:
    tokens          — dict-like proxy over the active mode's token set.
    set_mode(mode)  — switches the proxy between "dark" and "light".
    DARK, LIGHT     — the underlying frozen dicts (read-only by convention).
"""
from __future__ import annotations

from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Color tokens — both modes share names, only hex values switch.
# ---------------------------------------------------------------------------

_DARK_COLOR: dict[str, Any] = {
    "bg": {
        "canvas": "#0B0D10",
        "surface": {
            "1": "#14171C",
            "2": "#1B1F26",
            "3": "#232830",
            "raised": "#2A3038",
        },
    },
    "border": {
        "subtle": "#232830",
        "default": "#2D333D",
        "strong": "#3F4651",
    },
    "text": {
        "primary": "#F5F5F4",
        "secondary": "#A8A29E",
        "tertiary": "#78716C",
    },
    "accent": {
        "default": "#33B270",
        "hover": "#4BC588",
        "pressed": "#259458",
        "subtle": "#0E2818",
        "on": "#FFFFFF",
    },
    "success": "#16A34A",
    "warning": "#D97706",
    "danger": "#DC2626",
    "info": "#475569",
    "confirmed-yellow": "#FACC15",
    "revision-pink": "#EC4899",
    "demo-red": "#DC2626",
    "approved-green": "#16A34A",
    "mep-blue": "#0EA5E9",
    "scope-out": "#78716C",
}

_LIGHT_COLOR: dict[str, Any] = {
    "bg": {
        "canvas": "#FAFAF9",
        "surface": {
            "1": "#FFFFFF",
            "2": "#F4F4F2",
            "3": "#ECECEA",
            "raised": "#FFFFFF",
        },
    },
    "border": {
        "subtle": "#E7E5E4",
        "default": "#D6D3D1",
        "strong": "#A8A29E",
    },
    "text": {
        "primary": "#1C1917",
        "secondary": "#57534E",
        "tertiary": "#78716C",
    },
    "accent": {
        "default": "#16A34A",
        "hover": "#15803D",
        "pressed": "#166534",
        "subtle": "#ECFAF2",
        "on": "#FFFFFF",
    },
    "success": "#15803D",
    "warning": "#B45309",
    "danger": "#B91C1C",
    "info": "#334155",
    "confirmed-yellow": "#FACC15",
    "revision-pink": "#DB2777",
    "demo-red": "#B91C1C",
    "approved-green": "#15803D",
    "mep-blue": "#0284C7",
    "scope-out": "#A8A29E",
}


# ---------------------------------------------------------------------------
# Typography — type scale shared across modes.
# Each entry: size (px), line_height (px), weight, optional tracking (em),
# optional transform.
# ---------------------------------------------------------------------------

_FONT: dict[str, Any] = {
    "family": {
        "sans": "Geist",
        "mono": "Geist Mono",
    },
    "scale": {
        "caption": {
            "size": 11, "line_height": 16, "weight": 500,
            "tracking": 0.06, "transform": "uppercase",
        },
        "body-sm": {"size": 12, "line_height": 18, "weight": 400},
        "body": {"size": 13, "line_height": 20, "weight": 400},
        "body-lg": {"size": 14, "line_height": 22, "weight": 400},
        "h6": {"size": 14, "line_height": 20, "weight": 600},
        "h5": {"size": 16, "line_height": 24, "weight": 600},
        "h4": {"size": 18, "line_height": 26, "weight": 600},
        "h3": {"size": 22, "line_height": 30, "weight": 600},
        "h2": {"size": 28, "line_height": 36, "weight": 600},
        "h1": {"size": 36, "line_height": 44, "weight": 600},
        "mono-sm": {"size": 12, "line_height": 18, "weight": 400},
        "mono": {"size": 13, "line_height": 20, "weight": 400},
    },
}


# ---------------------------------------------------------------------------
# Spacing / radius / shadow / motion / border — shared across modes.
# Light-mode shadows are toned down via dedicated dicts below.
# ---------------------------------------------------------------------------

_SPACE: dict[int, int] = {
    0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 20,
    6: 24, 8: 32, 12: 48, 16: 64,
}

_RADIUS: dict[str, int] = {
    "none": 0, "sm": 4, "md": 6, "lg": 8, "xl": 12, "2xl": 16, "full": 9999,
}

# 1px borders are a universal carve-out from the "no hardcoded px" rule
# (the rule polices spacing/radii, not unit borders). They live as tokens
# so consumers can grep for them.
_BORDER_WIDTH: dict[str, int] = {"hairline": 1, "thick": 2}

_DARK_SHADOW: dict[str, str] = {
    "1": "0px 1px 2px rgba(0, 0, 0, 0.20)",
    "2": "0px 4px 8px rgba(0, 0, 0, 0.30)",
    "3": "0px 12px 24px rgba(0, 0, 0, 0.40)",
    "4": "0px 24px 48px rgba(0, 0, 0, 0.50)",
}

_LIGHT_SHADOW: dict[str, str] = {
    "1": "0px 1px 2px rgba(15, 23, 42, 0.06)",
    "2": "0px 4px 8px rgba(15, 23, 42, 0.10)",
    "3": "0px 12px 24px rgba(15, 23, 42, 0.14)",
    "4": "0px 24px 48px rgba(15, 23, 42, 0.20)",
}

_MOTION: dict[str, dict[str, Any]] = {
    "fast": {"duration": 120, "easing": "cubic-bezier(0.4, 0, 0.2, 1)"},
    "normal": {"duration": 200, "easing": "cubic-bezier(0.4, 0, 0.2, 1)"},
    "slow": {"duration": 320, "easing": "cubic-bezier(0.4, 0, 0.2, 1)"},
    "spring": {"duration": 400, "easing": "cubic-bezier(0.34, 1.56, 0.64, 1)"},
}


def _build_mode(color: Mapping[str, Any], shadow: Mapping[str, str]) -> dict[str, Any]:
    return {
        "color": dict(color),
        "font": dict(_FONT),
        "space": dict(_SPACE),
        "radius": dict(_RADIUS),
        "border": dict(_BORDER_WIDTH),
        "shadow": dict(shadow),
        "motion": dict(_MOTION),
    }


DARK: dict[str, Any] = _build_mode(_DARK_COLOR, _DARK_SHADOW)
LIGHT: dict[str, Any] = _build_mode(_LIGHT_COLOR, _LIGHT_SHADOW)


# ---------------------------------------------------------------------------
# Active-mode proxy.
# ---------------------------------------------------------------------------

_active_mode: str = "dark"


def set_mode(mode: str) -> None:
    """Switch the active token set. ``mode`` must be ``"dark"`` or ``"light"``."""
    global _active_mode
    if mode not in {"dark", "light"}:
        raise ValueError(f"Unknown theme mode: {mode!r}")
    _active_mode = mode


def get_mode() -> str:
    """Return the currently active mode name."""
    return _active_mode


class _TokenProxy:
    """Dict-like proxy that always returns the active mode's token tree.

    Implemented as a proxy (rather than a copy) so consumers can hold a
    reference at import time and still see updates after ``set_mode()`` runs.
    """

    def _active(self) -> dict[str, Any]:
        return DARK if _active_mode == "dark" else LIGHT

    # Mapping protocol — read-only; mutations would silently desync the modes.
    def __getitem__(self, key: str) -> Any:
        return self._active()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._active()

    def __iter__(self):
        return iter(self._active())

    def __len__(self) -> int:
        return len(self._active())

    def keys(self):
        return self._active().keys()

    def values(self):
        return self._active().values()

    def items(self):
        return self._active().items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._active().get(key, default)

    def __repr__(self) -> str:
        return f"<tokens proxy mode={_active_mode!r}>"


tokens = _TokenProxy()


__all__ = ["DARK", "LIGHT", "tokens", "set_mode", "get_mode"]
