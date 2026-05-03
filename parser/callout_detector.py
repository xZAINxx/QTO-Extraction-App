"""Detect detail callout strings (e.g. ``4/A-501``) on a PDF page.

A detail callout in architectural drawings references another detail on
another sheet — ``4/A-501`` means "Detail 4 on Sheet A-501". This module
runs a regex over ``page.get_text("words")`` and returns one entry per
matching word so the canvas can show a hover-tooltip with the target
sheet's thumbnail (the Wave 6 detail-bubble preview).

Kept deliberately small (well under 100 lines) — it's a one-trick helper
that the new MainWindow calls lazily on the page the user navigates to.
"""
from __future__ import annotations

import re
from typing import Any

import fitz

# Captures things like ``4/A501``, ``4/A-501``, ``12/A-501.2``. The dash
# between the discipline letter and the sheet number is optional. Word
# boundaries on each side avoid matching mid-token (e.g. inside a URL).
_CALLOUT_RE = re.compile(r"\b\d{1,2}/[A-Z]-?\d{3,4}(?:\.\d+)?\b")


def _sheet_id_from_match(match_text: str) -> str:
    """Extract the sheet portion (after ``/``) from a callout string.

    ``"4/A-501"`` → ``"A-501"``; ``"12/A501.2"`` → ``"A501.2"``. Caller
    can normalize further (strip the dash) when keying into a lookup.
    """
    _, _, sheet = match_text.partition("/")
    return sheet


def detect_callouts(page: Any) -> list[tuple[fitz.Rect, str, str]]:
    """Find all detail callouts on ``page``; return ``(rect, text, sheet_id)``.

    ``rect`` is the word's PDF-space bounding box (a ``fitz.Rect``).
    ``text`` is the matched callout literal (e.g. ``"4/A-501"``).
    ``sheet_id`` is the portion after ``/`` (e.g. ``"A-501"``) — the new
    MainWindow uses this to look up the target page number.

    Tolerates pages that have no extractable text (returns ``[]``) and
    word-tuples shorter than the canonical 8-tuple ``fitz`` exposes.
    """
    try:
        words = page.get_text("words")
    except Exception:
        return []
    if not words:
        return []

    out: list[tuple[fitz.Rect, str, str]] = []
    for word in words:
        if not word or len(word) < 5:
            continue
        text = word[4]
        if not isinstance(text, str):
            continue
        match = _CALLOUT_RE.search(text)
        if match is None:
            continue
        try:
            x0, y0, x1, y1 = float(word[0]), float(word[1]), float(word[2]), float(word[3])
        except (TypeError, ValueError):
            continue
        rect = fitz.Rect(x0, y0, x1, y1)
        callout_text = match.group(0)
        sheet_id = _sheet_id_from_match(callout_text)
        out.append((rect, callout_text, sheet_id))
    return out


__all__ = ["detect_callouts"]
