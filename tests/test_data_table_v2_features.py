"""Tests for Phase 2 commit 10: drag-drop reclassification + risk pills.

Mirrors the offscreen pytest pattern used by ``tests/test_data_table.py`` and
``tests/test_components_smoke.py``. Drag events are *not* triggered through
the actual Qt event loop — instead we call ``mimeData`` and ``dropMimeData``
directly with constructed indexes / payloads, which is both faster and less
flaky than synthesizing real drag gestures.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QModelIndex, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from core.qto_row import QTORow
from ui.components.data_table import (
    COL_DESCRIPTION,
    COL_RISK,
    QTOROW_INDEX_MIME,
    RISK_FLAGS_ROLE,
    RISK_FLAG_TAXONOMY,
    QtoDataTable,
    QtoTableModel,
    RiskFlagsDelegate,
)


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
def two_division_rows() -> list[QTORow]:
    """Six rows: 3 in 'Masonry' (indices 0-2), 3 in 'Demolition' (3-5).

    Used by drag-drop tests so the "drop row 0 onto row 5" scenario
    crosses a real division boundary.
    """
    rows: list[QTORow] = []
    for i in range(3):
        rows.append(
            QTORow(
                s_no=i + 1,
                description=f"Masonry item {i}",
                trade_division="Masonry",
                source_sheet=f"A-10{i}",
                source_page=10 + i,
                bbox=(float(i), 1.0, 2.0, 3.0),
                extraction_method="vector",
                confidence=0.9 - i * 0.05,
            )
        )
    for i in range(3):
        rows.append(
            QTORow(
                s_no=i + 4,
                description=f"Demo item {i}",
                trade_division="Demolition",
                source_sheet=f"D-20{i}",
                source_page=20 + i,
                bbox=(10.0 + i, 11.0, 12.0, 13.0),
                extraction_method="vision",
                confidence=0.5 + i * 0.05,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# QTORow risk_flags field
# ---------------------------------------------------------------------------


def test_qto_row_risk_flags_default_empty_list() -> None:
    row = QTORow()
    assert row.risk_flags == []
    # Each instance gets its own list — no shared mutable default.
    other = QTORow()
    other.risk_flags.append("spec_ambiguity")
    assert row.risk_flags == []


def test_qto_row_risk_flags_serializes_via_dataclass() -> None:
    row = QTORow(risk_flags=["spec_ambiguity", "by_others"])
    payload = asdict(row)
    assert payload["risk_flags"] == ["spec_ambiguity", "by_others"]
    # Round-trip through the constructor (mirrors ResultCache.load behaviour).
    rebuilt = QTORow(**payload)
    assert rebuilt.risk_flags == ["spec_ambiguity", "by_others"]


# ---------------------------------------------------------------------------
# Drag-drop: model
# ---------------------------------------------------------------------------


def test_qto_table_model_supports_move_drop_action(qapp) -> None:
    model = QtoTableModel([QTORow(description="x")])
    assert model.supportedDropActions() == Qt.DropAction.MoveAction


def test_qto_table_model_mime_types_includes_qtorow_indices(qapp) -> None:
    model = QtoTableModel([QTORow()])
    assert QTOROW_INDEX_MIME in model.mimeTypes()


def test_qto_table_model_mime_data_serializes_row_indices(
    qapp, two_division_rows
) -> None:
    model = QtoTableModel(two_division_rows)
    indexes = [model.index(0, COL_DESCRIPTION), model.index(2, COL_DESCRIPTION)]
    mime = model.mimeData(indexes)

    assert mime.hasFormat(QTOROW_INDEX_MIME)
    payload = json.loads(bytes(mime.data(QTOROW_INDEX_MIME)).decode("utf-8"))
    assert payload == [0, 2]


def test_qto_table_model_drop_mime_data_updates_trade_division(
    qapp, two_division_rows
) -> None:
    model = QtoTableModel(two_division_rows)
    mime = QMimeData()
    mime.setData(QTOROW_INDEX_MIME, json.dumps([0]).encode("utf-8"))
    parent = model.index(5, 0)  # drop on a Demolition row

    ok = model.dropMimeData(
        mime, Qt.DropAction.MoveAction, -1, -1, parent
    )

    assert ok is True
    assert model.row_at(0).trade_division == "Demolition"
    assert model.row_at(5).trade_division == "Demolition"  # target unchanged


def test_qto_table_model_drop_preserves_source_provenance(
    qapp, two_division_rows
) -> None:
    model = QtoTableModel(two_division_rows)
    snapshot = (
        two_division_rows[0].source_sheet,
        two_division_rows[0].source_page,
        two_division_rows[0].bbox,
        two_division_rows[0].extraction_method,
        two_division_rows[0].confidence,
    )
    mime = QMimeData()
    mime.setData(QTOROW_INDEX_MIME, json.dumps([0]).encode("utf-8"))
    parent = model.index(5, 0)

    model.dropMimeData(mime, Qt.DropAction.MoveAction, -1, -1, parent)

    moved = model.row_at(0)
    assert (
        moved.source_sheet,
        moved.source_page,
        moved.bbox,
        moved.extraction_method,
        moved.confidence,
    ) == snapshot


def test_qto_table_model_remove_rows_is_a_noop_for_internal_move(
    qapp, two_division_rows
) -> None:
    """``InternalMove`` makes Qt call removeRows post-drop; the model must
    swallow that call so reclassification doesn't actually delete rows."""
    model = QtoTableModel(two_division_rows)
    before = model.rowCount()
    assert model.removeRows(0, 1, QModelIndex()) is True
    assert model.rowCount() == before


# ---------------------------------------------------------------------------
# Drag-drop: composite widget signal
# ---------------------------------------------------------------------------


def test_qto_data_table_emits_rows_reclassified_signal(
    qapp, two_division_rows
) -> None:
    table = QtoDataTable()
    table.replace_rows(two_division_rows)
    spy = QSignalSpy(table.rows_reclassified)

    mime = QMimeData()
    mime.setData(QTOROW_INDEX_MIME, json.dumps([0, 1]).encode("utf-8"))
    parent = table.model().index(5, 0)
    table.model().dropMimeData(
        mime, Qt.DropAction.MoveAction, -1, -1, parent
    )

    assert len(spy) == 1
    payload = spy[0]
    assert sorted(list(payload[0])) == [0, 1]
    assert payload[1] == "Demolition"


# ---------------------------------------------------------------------------
# Risk flag column + role
# ---------------------------------------------------------------------------


def test_qto_table_model_has_risk_column_at_index_9(qapp) -> None:
    model = QtoTableModel([QTORow()])
    assert model.columnCount() == 10
    header = model.headerData(
        COL_RISK, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole
    )
    assert header == "RISK"
    assert COL_RISK == 9


def test_qto_table_model_risk_role_returns_flag_list(qapp) -> None:
    row = QTORow(risk_flags=["spec_ambiguity", "volatile_material"])
    model = QtoTableModel([row])
    flags = model.data(model.index(0, COL_RISK), RISK_FLAGS_ROLE)
    assert flags == ["spec_ambiguity", "volatile_material"]
    # Defensive copy — mutating the returned list must not leak back.
    flags.append("by_others")
    assert model.row_at(0).risk_flags == [
        "spec_ambiguity", "volatile_material"
    ]


# ---------------------------------------------------------------------------
# Risk flag mutation API (the context menu wires through these)
# ---------------------------------------------------------------------------


def test_qto_data_table_context_menu_add_risk_flag(qapp) -> None:
    rows = [
        QTORow(description="Wall A", trade_division="Masonry"),
        QTORow(description="Wall B", trade_division="Masonry"),
    ]
    table = QtoDataTable()
    table.replace_rows(rows)

    # Simulate the context-menu action firing for both rows.
    table.toggle_risk_flag([0, 1], "spec_ambiguity")

    assert table.model().row_at(0).risk_flags == ["spec_ambiguity"]
    assert table.model().row_at(1).risk_flags == ["spec_ambiguity"]

    # Toggling again removes (per the unanimous-toggle convention).
    table.toggle_risk_flag([0, 1], "spec_ambiguity")
    assert table.model().row_at(0).risk_flags == []
    assert table.model().row_at(1).risk_flags == []


def test_qto_data_table_clear_risk_flags_action_removes_all_flags(qapp) -> None:
    rows = [
        QTORow(description="W1", risk_flags=["spec_ambiguity", "by_others"]),
        QTORow(description="W2", risk_flags=["volatile_material"]),
    ]
    table = QtoDataTable()
    table.replace_rows(rows)
    table.clear_risk_flags([0, 1])
    assert table.model().row_at(0).risk_flags == []
    assert table.model().row_at(1).risk_flags == []


# ---------------------------------------------------------------------------
# Risk flag delegate paint
# ---------------------------------------------------------------------------


def test_risk_flags_delegate_paints_one_pill_per_flag(qapp, monkeypatch) -> None:
    """Patch QPainter primitives, hand the delegate a fake index that
    returns three risk flags, and assert it paints three rounded rects."""
    from PyQt6.QtWidgets import QStyledItemDelegate

    delegate = RiskFlagsDelegate()
    flags = ["spec_ambiguity", "volatile_material", "by_others"]

    fake_index = MagicMock()
    fake_index.column.return_value = COL_RISK
    fake_index.data.return_value = flags
    fake_index.isValid.return_value = True

    option = SimpleNamespace(
        rect=MagicMock(
            left=lambda: 0,
            top=lambda: 0,
            right=lambda: 800,
            height=lambda: 28,
        )
    )

    painter = MagicMock(spec=QPainter)
    # Stub out the base-class chrome paint so the call's strict argument
    # typing doesn't fight the MagicMock painter — we only care here
    # about the rounded-rect / text calls our delegate makes itself.
    monkeypatch.setattr(QStyledItemDelegate, "paint", lambda *a, **kw: None)

    delegate.paint(painter, option, fake_index)

    assert painter.drawRoundedRect.call_count == len(flags)
    # And one drawText per pill (label).
    assert painter.drawText.call_count == len(flags)


def test_risk_flag_taxonomy_covers_five_documented_flags() -> None:
    ids = [t[0] for t in RISK_FLAG_TAXONOMY]
    assert set(ids) == {
        "spec_ambiguity",
        "design_dev_drawing",
        "volatile_material",
        "low_qty_confidence",
        "by_others",
    }
