"""Tests for the Wave 6 detail-bubble preview (commit 12).

Covers:
    * The regex in ``parser.callout_detector`` matches the three common
      callout shapes (``N/A-NNN``, ``N/A-NNN.D``, ``N/ANNN``) and rejects
      pages without callouts.
    * ``PDFViewer.set_detail_callouts`` stores per-page entries on
      internal state without touching unrelated pages.
    * ``PDFViewer`` exposes the ``detail_jump_requested`` signal that
      ``ui/views/main_window.py`` connects to.

Mirrors the offscreen pytest pattern used by ``tests/test_trace_link.py``
and ``tests/test_sheet_rail.py``.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PyQt6.QtCore import QRectF
from PyQt6.QtWidgets import QApplication

from parser.callout_detector import detect_callouts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _fake_page(words: list[tuple]) -> MagicMock:
    """Build a ``fitz.Page``-shaped MagicMock with a controlled word list."""
    page = MagicMock()
    page.get_text.return_value = words
    return page


# ---------------------------------------------------------------------------
# detect_callouts — regex coverage
# ---------------------------------------------------------------------------


def test_detect_callouts_finds_simple_pattern():
    page = _fake_page([(10.0, 20.0, 60.0, 32.0, "4/A-501", 0, 0, 0)])
    out = detect_callouts(page)
    assert len(out) == 1
    rect, text, sheet_id = out[0]
    assert text == "4/A-501"
    assert sheet_id == "A-501"
    assert isinstance(rect, fitz.Rect)
    assert (rect.x0, rect.y0, rect.x1, rect.y1) == (10.0, 20.0, 60.0, 32.0)


def test_detect_callouts_finds_decimal_variant():
    page = _fake_page([(0.0, 0.0, 80.0, 12.0, "12/A-501.2", 0, 0, 0)])
    out = detect_callouts(page)
    assert len(out) == 1
    _, text, sheet_id = out[0]
    assert text == "12/A-501.2"
    assert sheet_id == "A-501.2"


def test_detect_callouts_handles_no_dash():
    page = _fake_page([(5.0, 5.0, 55.0, 17.0, "4/A501", 0, 0, 0)])
    out = detect_callouts(page)
    assert len(out) == 1
    _, text, sheet_id = out[0]
    assert text == "4/A501"
    assert sheet_id == "A501"


def test_detect_callouts_returns_empty_for_no_matches():
    page = _fake_page([
        (0.0, 0.0, 30.0, 10.0, "PLAN", 0, 0, 0),
        (0.0, 12.0, 50.0, 22.0, "NORTH", 0, 0, 0),
        (0.0, 24.0, 60.0, 34.0, "1\"=1'-0\"", 0, 0, 0),
    ])
    assert detect_callouts(page) == []


def test_detect_callouts_handles_empty_word_list():
    assert detect_callouts(_fake_page([])) == []


def test_detect_callouts_handles_get_text_exception():
    page = MagicMock()
    page.get_text.side_effect = RuntimeError("boom")
    assert detect_callouts(page) == []


# ---------------------------------------------------------------------------
# PDFViewer wiring
# ---------------------------------------------------------------------------


def test_pdf_viewer_set_detail_callouts_stores_per_page(qapp):
    from ui.pdf_viewer import PDFViewer
    viewer = PDFViewer()
    callouts_pg2 = [(QRectF(0.0, 0.0, 50.0, 12.0), "4/A-501", 0)]
    callouts_pg3 = [
        (QRectF(0.0, 0.0, 50.0, 12.0), "5/A-201", 0),
        (QRectF(60.0, 0.0, 110.0, 12.0), "6/A-201", 0),
    ]
    viewer.set_detail_callouts(2, callouts_pg2)
    viewer.set_detail_callouts(3, callouts_pg3)
    assert viewer._detail_callouts[2] == callouts_pg2
    assert viewer._detail_callouts[3] == callouts_pg3
    # Pages we never registered must not appear in the dict.
    assert 1 not in viewer._detail_callouts


def test_pdf_viewer_detail_jump_requested_signal_exists(qapp):
    from ui.pdf_viewer import PDFViewer
    viewer = PDFViewer()
    assert hasattr(viewer, "detail_jump_requested")
    received: list[int] = []
    viewer.detail_jump_requested.connect(lambda n: received.append(n))
    viewer.detail_jump_requested.emit(42)
    assert received == [42]


def test_pdf_viewer_set_detail_callouts_replaces_prior_entry(qapp):
    from ui.pdf_viewer import PDFViewer
    viewer = PDFViewer()
    viewer.set_detail_callouts(2, [(QRectF(0, 0, 10, 10), "4/A-101", 0)])
    viewer.set_detail_callouts(2, [(QRectF(0, 0, 20, 20), "5/A-202", 7)])
    assert len(viewer._detail_callouts[2]) == 1
    assert viewer._detail_callouts[2][0][1] == "5/A-202"
    assert viewer._detail_callouts[2][0][2] == 7
