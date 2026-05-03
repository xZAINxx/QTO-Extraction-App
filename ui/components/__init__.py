"""Component library for the redesigned QTO tool UI.

Each module exports a single primary widget class. Components are styled
exclusively through the QSS attribute-selector system in
``ui/theme/qss.py`` — they set dynamic properties (``variant``, ``size``,
etc.) and the theme paints them. No per-widget setStyleSheet calls except
where QSS itself is insufficient (e.g. transient pixel offsets in the
shimmer skeleton, or inline overrides on ad-hoc dismiss buttons).
"""
from __future__ import annotations

from .button import Button, ButtonSize, ButtonVariant
from .card import Card
from .empty_state import EmptyState
from .pill import Pill, PillVariant
from .skeleton import Skeleton, SkeletonShape
from .status_pill import StatusPill
from .toast import Toast, Toaster, ToastVariant

__all__ = [
    "Button",
    "ButtonSize",
    "ButtonVariant",
    "Card",
    "EmptyState",
    "Pill",
    "PillVariant",
    "Skeleton",
    "SkeletonShape",
    "StatusPill",
    "Toast",
    "Toaster",
    "ToastVariant",
]
