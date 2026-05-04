"""Tests for the Wave 4 TraceLink controller (commit 6).

Covers:
    * Bidirectional binding between a ``QtoDataTable`` row selection and the
      PDF viewer's ``go_to_page`` / ``pulse_highlight`` calls.
    * Canvas-click → table-row lookup via ``TraceLink.jump_to_row``.
    * The ``_dispatch_lock`` flag that breaks the row → canvas → row loop.
    * The ``PDFViewer.pulse_highlight`` brush alpha (~35%).
    * The ``PDFViewer.show_zone_overlay`` / ``hide_zone_overlay`` lifecycle.

Mirrors the offscreen pytest pattern used by ``tests/test_data_table.py``.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import QApplication

from core.qto_row import QTORow
from ui.components.data_table import QtoDataTable
from ui.controllers.trace_link import TraceLink, _bbox_overlap


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture
def sample_rows() -> list[QTORow]:
    return [
        QTORow(
            s_no=1,
            description="Brick veneer",
            qty=120.0,
            units="SQ FT",
            unit_price=14.5,
            source_page=2,
            source_sheet="A-101",
            confidence=0.92,
            bbox=(100.0, 200.0, 250.0, 260.0),
        ),
        QTORow(
            s_no=2,
            description="CMU partition demo",
            qty=80.0,
            units="LF",
            unit_price=18.0,
            source_page=5,
            source_sheet="A-102",
            confidence=0.65,
            bbox=(400.0, 100.0, 500.0, 180.0),
        ),
        QTORow(
            s_no=3,
            description="Glazing storefront",
            qty=4.0,
            units="EA",
            unit_price=2200.0,
            source_page=5,
            source_sheet="A-102",
            confidence=0.88,
            bbox=(50.0, 500.0, 220.0, 600.0),
        ),
    ]


@pytest.fixture
def populated_table(qapp, sample_rows) -> QtoDataTable:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    return table


@pytest.fixture
def mock_pdf_viewer() -> MagicMock:
    viewer = MagicMock()
    viewer.go_to_page = MagicMock()
    viewer.pulse_highlight = MagicMock()
    viewer.region_clicked = MagicMock()
    return viewer


# ---------------------------------------------------------------------------
# bbox helper
# ---------------------------------------------------------------------------


def test_bbox_overlap_returns_true_for_overlapping_boxes() -> None:
    a = (0.0, 0.0, 10.0, 10.0)
    b = (5.0, 5.0, 15.0, 15.0)
    assert _bbox_overlap(a, b) is True


def test_bbox_overlap_returns_true_for_contained_box() -> None:
    a = (0.0, 0.0, 100.0, 100.0)
    b = (10.0, 10.0, 20.0, 20.0)
    assert _bbox_overlap(a, b) is True


def test_bbox_overlap_returns_false_for_disjoint_boxes() -> None:
    a = (0.0, 0.0, 10.0, 10.0)
    b = (50.0, 50.0, 60.0, 60.0)
    assert _bbox_overlap(a, b) is False


def test_bbox_overlap_handles_touching_edges() -> None:
    a = (0.0, 0.0, 10.0, 10.0)
    b = (10.0, 0.0, 20.0, 10.0)
    assert _bbox_overlap(a, b) is True


# ---------------------------------------------------------------------------
# TraceLink construction + row → canvas
# ---------------------------------------------------------------------------


def test_trace_link_construction(qapp, populated_table, mock_pdf_viewer) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    assert link is not None
    assert link._dispatch_lock is False


def test_trace_link_row_selected_jumps_pdf_to_source_page(
    qapp, populated_table, mock_pdf_viewer
) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    # Select the second source row (page 5).
    proxy_idx = populated_table.proxy().mapFromSource(
        populated_table.model().index(1, 0)
    )
    populated_table.view().selectionModel().setCurrentIndex(
        proxy_idx,
        populated_table.view().selectionModel().SelectionFlag.SelectCurrent
        | populated_table.view().selectionModel().SelectionFlag.Rows,
    )
    qapp.processEvents()
    mock_pdf_viewer.go_to_page.assert_called_with(5)
    assert link is not None  # keep ref alive


def test_trace_link_row_with_bbox_pulses_highlight(
    qapp, populated_table, mock_pdf_viewer
) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    proxy_idx = populated_table.proxy().mapFromSource(
        populated_table.model().index(0, 0)
    )
    populated_table.view().selectionModel().setCurrentIndex(
        proxy_idx,
        populated_table.view().selectionModel().SelectionFlag.SelectCurrent
        | populated_table.view().selectionModel().SelectionFlag.Rows,
    )
    qapp.processEvents()
    mock_pdf_viewer.pulse_highlight.assert_called_once()
    page_arg, bbox_arg = mock_pdf_viewer.pulse_highlight.call_args.args
    assert page_arg == 2
    assert bbox_arg == (100.0, 200.0, 250.0, 260.0)
    assert link is not None


def test_trace_link_row_without_bbox_does_not_pulse(
    qapp, mock_pdf_viewer
) -> None:
    rows = [
        QTORow(
            s_no=1, description="No bbox here", qty=1.0, units="EA",
            source_page=3, source_sheet="A-103", confidence=0.9,
            bbox=None,
        ),
    ]
    table = QtoDataTable()
    table.replace_rows(rows)
    link = TraceLink(table=table, pdf_viewer=mock_pdf_viewer)
    proxy_idx = table.proxy().mapFromSource(table.model().index(0, 0))
    table.view().selectionModel().setCurrentIndex(
        proxy_idx,
        table.view().selectionModel().SelectionFlag.SelectCurrent
        | table.view().selectionModel().SelectionFlag.Rows,
    )
    qapp.processEvents()
    mock_pdf_viewer.go_to_page.assert_called_with(3)
    mock_pdf_viewer.pulse_highlight.assert_not_called()
    assert link is not None


# ---------------------------------------------------------------------------
# Canvas → row
# ---------------------------------------------------------------------------


def test_trace_link_jump_to_row_selects_matching_row(
    qapp, populated_table, mock_pdf_viewer
) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    # Click bbox overlapping the third row's bbox on page 5.
    link.jump_to_row(5, (100.0, 510.0, 130.0, 540.0))
    qapp.processEvents()
    selected = populated_table.selected_rows()
    assert selected == [2]


def test_trace_link_jump_to_row_no_match_keeps_selection(
    qapp, populated_table, mock_pdf_viewer
) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    link.jump_to_row(99, (0.0, 0.0, 5.0, 5.0))  # nothing on page 99
    qapp.processEvents()
    assert populated_table.selected_rows() == []
    assert link is not None


# ---------------------------------------------------------------------------
# Dispatch lock
# ---------------------------------------------------------------------------


def test_trace_link_dispatch_lock_prevents_feedback_loop(
    qapp, populated_table, mock_pdf_viewer
) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    link._dispatch_lock = True
    proxy_idx = populated_table.proxy().mapFromSource(
        populated_table.model().index(1, 0)
    )
    populated_table.view().selectionModel().setCurrentIndex(
        proxy_idx,
        populated_table.view().selectionModel().SelectionFlag.SelectCurrent
        | populated_table.view().selectionModel().SelectionFlag.Rows,
    )
    qapp.processEvents()
    mock_pdf_viewer.go_to_page.assert_not_called()
    mock_pdf_viewer.pulse_highlight.assert_not_called()


def test_trace_link_jump_to_row_respects_lock(
    qapp, populated_table, mock_pdf_viewer
) -> None:
    link = TraceLink(table=populated_table, pdf_viewer=mock_pdf_viewer)
    link._dispatch_lock = True
    link.jump_to_row(5, (100.0, 510.0, 130.0, 540.0))
    qapp.processEvents()
    assert populated_table.selected_rows() == []


# ---------------------------------------------------------------------------
# PDFViewer pulse_highlight + zone overlays
# ---------------------------------------------------------------------------


def _make_viewer_for_overlay(qapp, monkeypatch):
    """Construct a PDFViewer and stub out the doc-dependent helpers.

    The viewer has no PDF loaded — _pdf_to_scene_rect would crash because
    self._doc is None. We pin a tiny page rect and patch the rect mapper
    so overlay tests can run completely headless.
    """
    from ui.pdf_viewer import PDFViewer

    viewer = PDFViewer()
    # Pretend a page is loaded.
    viewer._doc = object()  # truthy sentinel
    fake_rect = MagicMock()
    fake_rect.is_empty = False
    viewer._page_rect = fake_rect
    viewer._page_num = 1

    def _fake_map(_pdf_rect):
        return QRectF(0.0, 0.0, 100.0, 100.0)

    monkeypatch.setattr(viewer, "_pdf_to_scene_rect", _fake_map)
    # go_to_page pokes self._doc[page_num-1] which would explode on a sentinel;
    # neutralise it for tests that just want pulse / zone behaviour.
    monkeypatch.setattr(viewer, "go_to_page", lambda *a, **kw: None)
    return viewer


def test_pdf_viewer_pulse_highlight_uses_yellow_at_35pct_alpha(qapp, monkeypatch) -> None:
    viewer = _make_viewer_for_overlay(qapp, monkeypatch)
    captured: dict = {}

    real_addrect = viewer._scene.addRect

    def _spy_addrect(rect, pen, brush):
        captured["pen"] = pen
        captured["brush"] = brush
        captured["rect"] = rect
        return real_addrect(rect, pen, brush)

    monkeypatch.setattr(viewer._scene, "addRect", _spy_addrect)
    viewer.pulse_highlight(1, (10.0, 10.0, 20.0, 20.0))
    assert "brush" in captured, "addRect was never invoked"
    color = captured["brush"].color()
    # ~35% of 255 = 89.25; we round to 89.
    assert color.alpha() == 89
    assert color.red() == 0xFA and color.green() == 0xCC and color.blue() == 0x15


def test_pdf_viewer_show_zone_overlay_renders_one_rect_per_zone(qapp, monkeypatch) -> None:
    viewer = _make_viewer_for_overlay(qapp, monkeypatch)

    fake_zone = types.SimpleNamespace(rect=MagicMock(), label="x", score=0.0)
    zones = types.SimpleNamespace(
        title_block=fake_zone,
        legends=[fake_zone],
        schedules=[fake_zone, fake_zone],
        plan_bodies=[],
        notes=[],
    )
    viewer.show_zone_overlay(1, zones)
    # 1 title + 1 legend + 2 schedules = 4 zone items.
    assert len(viewer._zone_items) == 4
    assert viewer.zone_overlay_visible is True


def test_pdf_viewer_hide_zone_overlay_clears_rects(qapp, monkeypatch) -> None:
    viewer = _make_viewer_for_overlay(qapp, monkeypatch)
    fake_zone = types.SimpleNamespace(rect=MagicMock())
    zones = types.SimpleNamespace(
        title_block=fake_zone,
        legends=[fake_zone, fake_zone],
        schedules=[],
        plan_bodies=[],
        notes=[],
    )
    viewer.show_zone_overlay(1, zones)
    assert len(viewer._zone_items) == 3
    viewer.hide_zone_overlay()
    assert viewer._zone_items == []
    assert viewer.zone_overlay_visible is False


def test_pdf_viewer_show_zone_overlay_replaces_previous(qapp, monkeypatch) -> None:
    viewer = _make_viewer_for_overlay(qapp, monkeypatch)
    fake_zone = types.SimpleNamespace(rect=MagicMock())
    zones_a = types.SimpleNamespace(
        title_block=fake_zone, legends=[fake_zone], schedules=[],
        plan_bodies=[], notes=[],
    )
    zones_b = types.SimpleNamespace(
        title_block=None, legends=[], schedules=[fake_zone],
        plan_bodies=[fake_zone], notes=[fake_zone],
    )
    viewer.show_zone_overlay(1, zones_a)
    assert len(viewer._zone_items) == 2
    viewer.show_zone_overlay(1, zones_b)
    assert len(viewer._zone_items) == 3
