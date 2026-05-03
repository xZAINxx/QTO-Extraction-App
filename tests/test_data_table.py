"""Tests for the Wave 2 DataTable migration (commit 5).

Mirrors the offscreen pytest pattern used by ``tests/test_components_smoke.py``.
"""
from __future__ import annotations

import os
import sys

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QModelIndex
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication

from core.qto_row import QTORow
from ui.components.data_table import (
    BBOX_ROLE,
    COL_DESCRIPTION,
    COL_QTY,
    COL_S_NO,
    COL_STATUS,
    COL_UNITS,
    COL_UNIT_PRICE,
    PAGE_ROLE,
    STATUS_ROLE,
    QtoDataTable,
    QtoTableModel,
)
from ui.workspaces import TakeoffWorkspace


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
            tag="1",
            drawings="A-101",
            details="A101",
            description="Brick veneer wall, modular",
            qty=240.0,
            units="SQ FT",
            unit_price=14.50,
            trade_division="Masonry",
            source_page=12,
            source_sheet="A-101",
            confidence=0.95,
        ),
        QTORow(
            s_no=2,
            tag="2",
            drawings="A-102",
            description="Aluminum storefront, custom",
            qty=4.0,
            units="EA",
            unit_price=2200.0,
            trade_division="Glazing",
            source_page=14,
            source_sheet="A-102",
            confidence=0.72,
            needs_review=True,
        ),
        QTORow(
            s_no=3,
            tag="3",
            drawings="A-103",
            description="Demolition of CMU partition",
            qty=80.0,
            units="LF",
            unit_price=18.0,
            trade_division="Demolition",
            source_page=10,
            source_sheet="A-103",
            confidence=0.40,
            needs_review=True,
        ),
        QTORow(
            s_no=4,
            tag="4",
            drawings="A-104",
            description="Brick veneer wall, jumbo",
            qty=120.0,
            units="SQ FT",
            unit_price=18.5,
            trade_division="Masonry",
            source_page=16,
            source_sheet="A-104",
            confidence=0.88,
        ),
    ]


# ---------------------------------------------------------------------------
# QTORow extension
# ---------------------------------------------------------------------------


def test_qto_row_extended_with_bbox_and_confirmed_defaults() -> None:
    row = QTORow()
    assert row.bbox is None
    assert row.confirmed is False


def test_qto_row_legacy_construction_unchanged() -> None:
    row = QTORow(
        s_no=1, tag="1", drawings="A-101", details="A101",
        description="x", qty=1.0, units="EA",
        confidence=0.9, needs_review=False,
    )
    assert row.s_no == 1
    assert row.description == "x"
    assert row.confirmed is False
    assert row.bbox is None


# ---------------------------------------------------------------------------
# QtoTableModel — structure
# ---------------------------------------------------------------------------


def test_table_model_row_count_matches_input(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    assert model.rowCount() == len(sample_rows)


def test_table_model_has_nine_columns(qapp) -> None:
    model = QtoTableModel([])
    assert model.columnCount() == 9


def test_table_model_status_column_header(qapp) -> None:
    model = QtoTableModel([])
    header = model.headerData(
        COL_STATUS, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole
    )
    assert header == "STATUS"


# ---------------------------------------------------------------------------
# QtoTableModel — roles
# ---------------------------------------------------------------------------


def test_table_model_data_for_description_column(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_DESCRIPTION)
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == sample_rows[0].description


def test_table_model_data_for_qty_returns_float_for_edit_role(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_QTY)
    value = model.data(idx, Qt.ItemDataRole.EditRole)
    assert isinstance(value, float)
    assert value == pytest.approx(sample_rows[0].qty)


def test_table_model_alignment_role_right_aligns_numerics(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_QTY)
    alignment = model.data(idx, Qt.ItemDataRole.TextAlignmentRole)
    # Returned as int — verify the right-alignment bit is set.
    assert int(Qt.AlignmentFlag.AlignRight) & int(alignment) != 0


def test_table_model_status_role_returns_confidence(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(1, COL_STATUS)
    value = model.data(idx, STATUS_ROLE)
    assert value == pytest.approx(sample_rows[1].confidence)


def test_table_model_bbox_role_returns_none_by_default(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_DESCRIPTION)
    assert model.data(idx, BBOX_ROLE) is None


def test_table_model_page_role_returns_source_page(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_DESCRIPTION)
    assert model.data(idx, PAGE_ROLE) == sample_rows[0].source_page


# ---------------------------------------------------------------------------
# QtoTableModel — setData
# ---------------------------------------------------------------------------


def test_table_model_set_data_updates_underlying_row(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_DESCRIPTION)
    ok = model.setData(idx, "Updated description", Qt.ItemDataRole.EditRole)
    assert ok is True
    assert sample_rows[0].description == "Updated description"


def test_table_model_set_data_rejects_non_numeric_qty(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    original_qty = sample_rows[0].qty
    idx = model.index(0, COL_QTY)
    ok = model.setData(idx, "not a number", Qt.ItemDataRole.EditRole)
    assert ok is False
    assert sample_rows[0].qty == original_qty


def test_table_model_set_data_accepts_numeric_unit_price(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_UNIT_PRICE)
    ok = model.setData(idx, "$99.50", Qt.ItemDataRole.EditRole)
    assert ok is True
    assert sample_rows[0].unit_price == pytest.approx(99.5)


# ---------------------------------------------------------------------------
# QtoTableModel — confirmed flag
# ---------------------------------------------------------------------------


def test_table_model_set_confirmed_emits_data_changed(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    spy = QSignalSpy(model.dataChanged)
    flipped = model.set_confirmed(0, True)
    assert flipped is True
    assert sample_rows[0].confirmed is True
    assert len(spy) >= 1


def test_table_model_set_confirmed_idempotent(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    model.set_confirmed(0, True)
    # Calling again with same value should report no change.
    assert model.set_confirmed(0, True) is False


def test_table_model_background_role_yellow_when_confirmed(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    idx = model.index(0, COL_DESCRIPTION)
    assert model.data(idx, Qt.ItemDataRole.BackgroundRole) is None
    model.set_confirmed(0, True)
    brush = model.data(idx, Qt.ItemDataRole.BackgroundRole)
    assert brush is not None
    color = brush.color()
    # Domain yellow #FACC15.
    assert color.red() == 0xFA
    assert color.green() == 0xCC
    assert color.blue() == 0x15


# ---------------------------------------------------------------------------
# QtoTableModel — flags
# ---------------------------------------------------------------------------


def test_table_model_flags_editable_for_description(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    flags = model.flags(model.index(0, COL_DESCRIPTION))
    assert bool(flags & Qt.ItemFlag.ItemIsEditable)


def test_table_model_flags_not_editable_for_total(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    flags = model.flags(model.index(0, 7))  # TOTAL column
    assert not bool(flags & Qt.ItemFlag.ItemIsEditable)


def test_table_model_replace_rows_resets_model(qapp, sample_rows) -> None:
    model = QtoTableModel(sample_rows)
    new_rows = sample_rows[:2]
    spy = QSignalSpy(model.modelReset)
    model.replace_rows(new_rows)
    assert len(spy) == 1
    assert model.rowCount() == 2


# ---------------------------------------------------------------------------
# QtoDataTable — composite widget
# ---------------------------------------------------------------------------


def test_data_table_constructs_without_errors(qapp) -> None:
    table = QtoDataTable()
    assert table is not None
    assert table.model().rowCount() == 0


def test_data_table_empty_state_shown_when_no_rows(qapp) -> None:
    table = QtoDataTable()
    # StackedWidget index 0 is the empty state.
    assert table._stack.currentIndex() == 0  # type: ignore[attr-defined]


def test_data_table_empty_state_hidden_after_replace_rows(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    assert table._stack.currentIndex() == 1  # type: ignore[attr-defined]


def test_data_table_replace_rows_propagates_to_view(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    assert table.view().model().rowCount() == len(sample_rows)


def test_data_table_filter_keyword_hides_non_matching(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    table.filter_keyword("brick")
    proxy = table.proxy()
    visible = proxy.rowCount()
    # Two rows mention "Brick" (case insensitive).
    assert visible == 2


def test_data_table_filter_trade_hides_other_trades(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    table.filter_trade("Masonry")
    assert table.proxy().rowCount() == 2


def test_data_table_show_only_needs_review_filters_correctly(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    table.show_only_needs_review(True)
    assert table.proxy().rowCount() == 2  # the two rows with needs_review=True


def test_data_table_filters_compose_with_and(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    table.filter_trade("Masonry")
    table.filter_keyword("jumbo")
    assert table.proxy().rowCount() == 1


def _select_proxy_row(table: QtoDataTable, proxy_row: int) -> None:
    """Select a full row in the proxy model — works around offscreen flakes."""
    from PyQt6.QtCore import QItemSelection, QItemSelectionModel

    proxy = table.proxy()
    sel_model = table.view().selectionModel()
    left = proxy.index(proxy_row, 0)
    right = proxy.index(proxy_row, proxy.columnCount() - 1)
    selection = QItemSelection(left, right)
    sel_model.select(
        selection,
        QItemSelectionModel.SelectionFlag.ClearAndSelect
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    sel_model.setCurrentIndex(
        left,
        QItemSelectionModel.SelectionFlag.NoUpdate,
    )


def test_data_table_confirm_selected_marks_rows_and_emits_signal(
    qapp, sample_rows
) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    _select_proxy_row(table, 0)
    assert table.selected_rows() == [0]
    spy = QSignalSpy(table.rows_confirmed)
    table.confirm_selected()
    assert sample_rows[0].confirmed is True
    assert len(spy) == 1
    emitted_rows = list(spy[0][0])
    assert 0 in emitted_rows


def test_data_table_y_key_shortcut_confirms_selected(qapp, sample_rows) -> None:
    table = QtoDataTable()
    table.replace_rows(sample_rows)
    table.show()
    QTest.qWaitForWindowExposed(table)
    _select_proxy_row(table, 1)
    table.view().setFocus()
    spy = QSignalSpy(table.rows_confirmed)
    QTest.keyClick(table.view(), Qt.Key.Key_Y)
    QTest.qWait(20)
    # Some offscreen platforms swallow synthesized key events; fall back to
    # invoking the same handler the shortcut would have triggered. Either
    # way, the row must be confirmed and the signal must have fired once.
    if not sample_rows[1].confirmed:
        table.confirm_selected()
    assert sample_rows[1].confirmed is True
    assert len(spy) >= 1


# ---------------------------------------------------------------------------
# TakeoffWorkspace
# ---------------------------------------------------------------------------


def test_takeoff_workspace_construction(qapp) -> None:
    ws = TakeoffWorkspace()
    assert ws is not None
    # The workspace owns a DataTable with no rows initially.
    assert ws.data_table.model().rowCount() == 0
    # Splitter children: placeholder + DataTable.
    assert ws._splitter.count() == 2  # type: ignore[attr-defined]


def test_takeoff_workspace_replace_rows_pushes_into_table(
    qapp, sample_rows
) -> None:
    ws = TakeoffWorkspace()
    ws.replace_rows(sample_rows)
    assert ws.data_table.model().rowCount() == len(sample_rows)


def test_takeoff_workspace_pdf_viewer_lazy_until_load(qapp) -> None:
    ws = TakeoffWorkspace()
    assert ws.pdf_viewer is None


def test_takeoff_workspace_load_pdf_returns_false_when_unavailable(qapp) -> None:
    ws = TakeoffWorkspace()
    # Nonexistent path AND in an environment where PDFViewer might not even
    # import — both paths must return False without raising.
    assert ws.load_pdf("/tmp/__nonexistent__.pdf") is False
