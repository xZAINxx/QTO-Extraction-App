"""Tests for the command palette (Wave 5 commit 8 — dapper-pebble plan).

Headless / module-scoped ``QApplication`` pattern matching sibling tests.
The palette is a frameless modal dialog with a fuzzy-search input over a
mixed index of rows, sheets, divisions, and registered commands.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_palette_construction(qapp) -> None:
    from ui.components.command_palette import CommandPalette
    palette = CommandPalette()
    try:
        size_hint = palette.sizeHint()
        assert size_hint.isValid()
        assert palette.width() > 0
        assert palette.height() > 0
    finally:
        palette.deleteLater()


# ---------------------------------------------------------------------------
# Index population — empty query shows everything when count is small.
# ---------------------------------------------------------------------------


def test_palette_set_index_populates_results(qapp) -> None:
    from ui.components.command_palette import CommandPalette

    palette = CommandPalette()
    try:
        items = [
            {"type": "row", "label": f"DESC {i}", "subtitle": f"sub {i}", "payload": i}
            for i in range(5)
        ]
        palette.set_index(items)
        results = palette.results_widget()
        # With empty query and only 5 items, all should be visible.
        assert results.count() == 5
    finally:
        palette.deleteLater()


# ---------------------------------------------------------------------------
# Fuzzy search.
# ---------------------------------------------------------------------------


def test_palette_fuzzy_matches_partial_query(qapp) -> None:
    from ui.components.command_palette import CommandPalette

    palette = CommandPalette()
    try:
        items = [
            {"type": "row", "label": "GYPSUM BOARD", "subtitle": "DRAWINGS · A-101", "payload": 0},
            {"type": "row", "label": "EPDM ROOF", "subtitle": "DRAWINGS · R-100", "payload": 1},
            {"type": "row", "label": "PAINT WALLS", "subtitle": "DRAWINGS · A-200", "payload": 2},
        ]
        palette.set_index(items)
        palette.search_input().setText("gyps")
        # Force any debounce timer to flush.
        palette._apply_filter()
        results = palette.results_widget()
        assert results.count() >= 1
        first_item = results.item(0)
        assert first_item is not None
        # The item carries its source dict via UserRole.
        chosen = first_item.data(Qt.ItemDataRole.UserRole)
        assert chosen is not None
        assert chosen["label"] == "GYPSUM BOARD"
    finally:
        palette.deleteLater()


# ---------------------------------------------------------------------------
# Keyboard navigation.
# ---------------------------------------------------------------------------


def test_palette_navigates_with_arrow_keys(qapp) -> None:
    from ui.components.command_palette import CommandPalette

    palette = CommandPalette()
    try:
        items = [
            {"type": "command", "label": f"CMD {i}", "subtitle": "", "payload": i}
            for i in range(4)
        ]
        palette.set_index(items)
        palette.show()
        QTest.qWait(20)
        results = palette.results_widget()
        # After populating, row 0 is auto-selected.
        assert results.currentRow() == 0
        QTest.keyClick(palette, Qt.Key.Key_Down)
        assert results.currentRow() == 1
        QTest.keyClick(palette, Qt.Key.Key_Down)
        assert results.currentRow() == 2
        QTest.keyClick(palette, Qt.Key.Key_Up)
        assert results.currentRow() == 1
    finally:
        palette.deleteLater()


# ---------------------------------------------------------------------------
# Enter selection emits item_chosen.
# ---------------------------------------------------------------------------


def test_palette_enter_emits_item_chosen(qapp) -> None:
    from ui.components.command_palette import CommandPalette

    palette = CommandPalette()
    try:
        items = [
            {"type": "command", "label": "TOGGLE THEME", "subtitle": "", "payload": "theme"},
            {"type": "row", "label": "ROW B", "subtitle": "x", "payload": 7},
        ]
        palette.set_index(items)
        palette.show()
        QTest.qWait(20)
        spy = QSignalSpy(palette.item_chosen)
        QTest.keyClick(palette, Qt.Key.Key_Return)
        assert len(spy) == 1
        chosen = spy[0][0]
        assert isinstance(chosen, dict)
        assert chosen["label"] == "TOGGLE THEME"
        assert chosen["payload"] == "theme"
    finally:
        palette.deleteLater()


# ---------------------------------------------------------------------------
# Escape closes the palette.
# ---------------------------------------------------------------------------


def test_palette_escape_closes_dialog(qapp) -> None:
    from ui.components.command_palette import CommandPalette

    palette = CommandPalette()
    try:
        palette.set_index([
            {"type": "command", "label": "HI", "subtitle": "", "payload": None},
        ])
        palette.show()
        QTest.qWait(20)
        assert palette.isVisible()
        QTest.keyClick(palette, Qt.Key.Key_Escape)
        QTest.qWait(20)
        assert not palette.isVisible()
    finally:
        palette.deleteLater()


# ---------------------------------------------------------------------------
# build_palette_index helper.
# ---------------------------------------------------------------------------


def test_build_palette_index_combines_all_sources(qapp) -> None:
    from ui.components.command_palette import build_palette_index
    from core.qto_row import QTORow

    rows = [
        QTORow(
            s_no=1, drawings="A-101", description="GYPSUM BOARD",
            qty=250, units="SF", trade_division="DIV 09",
            source_sheet="A-101", source_page=3,
        ),
        QTORow(
            s_no=2, drawings="A-102", description="EPDM ROOF",
            qty=1000, units="SF", trade_division="DIV 07",
            source_sheet="A-102", source_page=4,
        ),
    ]
    commands = [
        {"label": "Toggle theme", "subtitle": "Light/dark", "payload": lambda: None},
        {"label": "Open PDF", "subtitle": "From disk", "payload": lambda: None},
    ]
    sheet_titles = {1: "A-101", 2: "A-102", 3: "A-201"}
    divisions = ["DIV 07", "DIV 09"]

    index = build_palette_index(
        rows=rows,
        sheet_count=3,
        sheet_titles=sheet_titles,
        divisions=divisions,
        commands=commands,
    )
    # Counts: 2 rows + 3 sheets + 2 divs + 2 commands = 9 entries minimum.
    types = [item["type"] for item in index]
    assert types.count("row") == 2
    assert types.count("sheet") == 3
    assert types.count("division") == 2
    assert types.count("command") == 2
    # Every entry has the four mandatory keys.
    for item in index:
        assert set(item.keys()) >= {"type", "label", "subtitle", "payload"}


def test_build_palette_index_empty_inputs_returns_empty_list(qapp) -> None:
    from ui.components.command_palette import build_palette_index

    assert build_palette_index() == []
    assert build_palette_index(rows=[], commands=[], divisions=[]) == []


# ---------------------------------------------------------------------------
# Command type — palette emits the dict but doesn't auto-call the payload.
# ---------------------------------------------------------------------------


def test_palette_command_type_callable_invoked_via_test_helper(qapp) -> None:
    """The palette emits item_chosen — invocation belongs to the parent slot."""
    from ui.components.command_palette import CommandPalette

    callable_mock = MagicMock()
    palette = CommandPalette()
    try:
        items = [
            {
                "type": "command",
                "label": "RUN MOCK",
                "subtitle": "",
                "payload": callable_mock,
            },
        ]
        palette.set_index(items)
        palette.show()
        QTest.qWait(20)
        spy = QSignalSpy(palette.item_chosen)
        QTest.keyClick(palette, Qt.Key.Key_Return)
        # Palette should have emitted the item dict but not invoked the callable.
        assert len(spy) == 1
        callable_mock.assert_not_called()
        chosen = spy[0][0]
        assert chosen["payload"] is callable_mock
    finally:
        palette.deleteLater()
