"""Wave 6 commit 11 — CoverageWorkspace tests.

Headless / module-scoped ``QApplication`` pattern, matching
``test_cockpit_workspace.py`` and ``test_diff_workspace.py``. The
workspace is pure derived state: no on-disk persistence to clean up,
so ``tmp_path`` is unused.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(qty: float, unit_price: float, division: str, source_sheet: str = ""):
    from core.qto_row import QTORow

    return QTORow(
        s_no=1, tag="T", description="d",
        qty=qty, units="LF", unit_price=unit_price,
        trade_division=division, source_sheet=source_sheet,
    )


def _walk_rows(layout, object_name: str) -> list:
    """Return every direct child widget under ``layout`` with the given name."""
    out = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        w = item.widget()
        if w is not None and w.objectName() == object_name:
            out.append(w)
    return out


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_coverage_construction(qapp) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    try:
        assert ws.sizeHint().isValid()
        # The summary card label exists and renders an empty-rows default.
        summary = ws.findChild(object, "coverageSummaryLabel")
        assert summary is not None
        assert "0 rows" in summary.text()
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Division breakdown math
# ---------------------------------------------------------------------------


def test_coverage_division_breakdown_counts_rows_per_division(qapp) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    try:
        rows = [
            _row(10, 5.0, division="DIVISION 03"),  # count=1, $50
            _row(4, 12.5, division="DIVISION 03"),  # count=2, $50
            _row(1, 2.0, division="DIVISION 03"),   # count=3, $2
            _row(100, 0.75, division="DIVISION 05"),  # count=1, $75
            _row(2, 50.0, division="DIVISION 05"),    # count=2, $100
        ]
        ws.set_rows(rows)
        # Internal counts reflect the buckets exactly.
        assert ws._by_division["DIVISION 03"][0] == 3
        assert ws._by_division["DIVISION 03"][1] == pytest.approx(102.0)
        assert ws._by_division["DIVISION 05"][0] == 2
        assert ws._by_division["DIVISION 05"][1] == pytest.approx(175.0)
    finally:
        ws.deleteLater()


def test_coverage_flags_empty_divisions_with_zero_rows_first(qapp) -> None:
    from ui.workspaces.coverage_workspace import (
        _CSI_DIVISIONS, CoverageWorkspace,
    )

    ws = CoverageWorkspace()
    try:
        rows = [
            _row(1, 1.0, division="DIVISION 03"),
            _row(1, 1.0, division="DIVISION 09"),
            _row(1, 1.0, division="DIVISION 26"),
        ]
        ws.set_rows(rows)
        rendered: list[tuple[str, int]] = []
        for i in range(ws._division_layout.count()):
            item = ws._division_layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None or w.objectName() != "coverageDivisionRow":
                continue
            rendered.append((
                str(w.property("divisionName")),
                int(w.property("rowCount") or 0),
            ))
        # All 16 canonical CSI divisions appear in the rendered list.
        assert len(rendered) == len(_CSI_DIVISIONS)
        # Empty CSI divisions sort to the top — first 13 entries are zero-row.
        empties_at_top = [r for r in rendered[:13] if r[1] == 0]
        assert len(empties_at_top) == 13, rendered
        # The three populated divisions appear after the empty block.
        non_empty_names = {n for n, c in rendered if c > 0}
        assert non_empty_names == {"DIVISION 03", "DIVISION 09", "DIVISION 26"}
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Sheet roster — productive sheets with zero rows
# ---------------------------------------------------------------------------


def test_coverage_sheet_with_plan_pages_but_zero_rows_flagged_maybe_missed(
    qapp,
) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    try:
        # Two productive plan pages; one produced rows, one didn't. Only
        # the silent one should get the danger pill.
        rows = [_row(5, 1.0, division="DIVISION 03", source_sheet="A-101")]
        ws.set_rows(rows)
        ws.set_sheets({
            1: {"page_type": "PLAN_DEMO", "sheet_id": "A-100", "skip": False},
            2: {"page_type": "PLAN_CONSTRUCTION", "sheet_id": "A-101", "skip": False},
            3: {"page_type": "SCHEDULE", "sheet_id": "A-102", "skip": False},
        })
        maybe_missed = ws.findChildren(object, "coverageSheetMaybeMissedPill")
        # A-100 (PLAN_DEMO, 0 rows) and A-102 (SCHEDULE, 0 rows) should
        # flag; A-101 has rows so it doesn't.
        assert len(maybe_missed) == 2
    finally:
        ws.deleteLater()


def test_coverage_sheet_skipped_pages_get_neutral_pill(qapp) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    try:
        ws.set_sheets({
            1: {"page_type": "TITLE", "sheet_id": "A-000", "skip": True},
            2: {"page_type": "INDEX", "sheet_id": "A-001", "skip": True},
        })
        skipped = ws.findChildren(object, "coverageSheetSkippedPill")
        maybe_missed = ws.findChildren(object, "coverageSheetMaybeMissedPill")
        assert len(skipped) == 2
        assert maybe_missed == []
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Summary card
# ---------------------------------------------------------------------------


def test_coverage_summary_card_shows_totals(qapp) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    try:
        rows = [
            _row(1, 1.0, division="DIVISION 03"),
            _row(1, 1.0, division="DIVISION 03"),
            _row(1, 1.0, division="DIVISION 09"),
        ]
        ws.set_rows(rows)
        ws.set_sheets({
            1: {"page_type": "PLAN_CONSTRUCTION", "sheet_id": "A-101", "skip": False},
            2: {"page_type": "TITLE", "sheet_id": "A-100", "skip": True},
        })
        text = ws._summary_label.text()
        # 3 rows total, 2 non-empty divisions, 2 sheets in the roster.
        assert "3 rows" in text
        assert "2 divisions" in text
        assert "2 sheets" in text
        assert "%" in text  # coverage percentage rendered
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Refresh signal
# ---------------------------------------------------------------------------


def test_coverage_refresh_signal_fires_on_button_click(qapp) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    captured: list[bool] = []
    ws.refresh_requested.connect(lambda: captured.append(True))
    try:
        btn = ws.findChild(object, "coverageRefreshBtn")
        assert btn is not None
        btn.click()
        assert captured == [True]
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Project name plumbing
# ---------------------------------------------------------------------------


def test_coverage_set_project_name_updates_label(qapp) -> None:
    from ui.workspaces.coverage_workspace import CoverageWorkspace

    ws = CoverageWorkspace()
    try:
        ws.set_project_name("Acme Tower")
        label = ws.findChild(object, "coverageProjectLabel")
        assert label is not None
        assert label.text() == "Acme Tower"
    finally:
        ws.deleteLater()
