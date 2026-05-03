"""Wave 5 commit 9 — CockpitWorkspace tests.

The CockpitWorkspace is the bid-day cockpit: total + countdown at the
top, division breakdown bar chart, exclusions free-text, markup
sliders, and a sub-bid table. We follow the same headless /
module-scoped ``QApplication`` pattern as ``test_layout_shell.py`` and
``test_diff_workspace.py`` — no pytest-qt dep, just an offscreen Qt
platform plugin. Persistence tests use ``tmp_path`` for the cache_dir
override so no shared on-disk state leaks between runs.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

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


def _row(qty: float, unit_price: float, division: str = "Concrete"):
    from core.qto_row import QTORow

    return QTORow(
        s_no=1, tag="T", description="d", qty=qty, units="LF",
        unit_price=unit_price, trade_division=division,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_cockpit_construction(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        assert ws.sizeHint().isValid()
        # Total label exists and shows the empty-row default.
        total = ws.findChild(object, "cockpitTotalLabel")
        assert total is not None
        assert "$0.00" in total.text()
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Division breakdown math
# ---------------------------------------------------------------------------


def test_cockpit_set_rows_calculates_division_breakdown(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        rows = [
            _row(10, 5.0, division="Concrete"),    # 50
            _row(4, 12.5, division="Concrete"),    # 50  → Concrete=100
            _row(100, 0.75, division="Steel"),     # 75  → Steel=75
        ]
        ws.set_rows(rows)
        assert ws._by_division["Concrete"] == pytest.approx(100.0)
        assert ws._by_division["Steel"] == pytest.approx(75.0)
        # No phantom divisions.
        assert set(ws._by_division.keys()) == {"Concrete", "Steel"}
    finally:
        ws.deleteLater()


def test_cockpit_division_breakdown_sorts_descending_by_amount(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        rows = [
            _row(10, 5.0, division="Concrete"),    # 50
            _row(100, 0.75, division="Steel"),     # 75
            _row(20, 10.0, division="Masonry"),    # 200
        ]
        ws.set_rows(rows)
        # Walk the rendered division rows; order must be by amount desc:
        # Masonry (200) > Steel (75) > Concrete (50).
        rendered: list[str] = []
        for i in range(ws._division_layout.count()):
            item = ws._division_layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None or w.objectName() != "cockpitDivisionRow":
                continue
            rendered.append(str(w.property("divisionName")))
        assert rendered == ["Masonry", "Steel", "Concrete"]
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Total + markup math (additive)
# ---------------------------------------------------------------------------


def test_cockpit_total_includes_markup(qapp) -> None:
    """Additive markup: 10000 base * (1 + 0.25) == 12500."""
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        ws.set_rows([_row(1000, 10.0, division="Concrete")])  # base = 10000
        ws._overhead_slider.setValue(10)
        ws._profit_slider.setValue(10)
        ws._contingency_slider.setValue(5)
        assert ws.calculate_total() == pytest.approx(12500.0)
    finally:
        ws.deleteLater()


def test_cockpit_zero_unit_price_rows_handled_gracefully(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        rows = [
            _row(10, 0.0, division="Concrete"),
            _row(100, 0.0, division="Steel"),
            _row(5, 4.0, division="Steel"),  # 20
        ]
        ws.set_rows(rows)
        assert ws._by_division["Concrete"] == pytest.approx(0.0)
        assert ws._by_division["Steel"] == pytest.approx(20.0)
        # No NaN / divide-by-zero leaks into the formatted total.
        total_label = ws.findChild(object, "cockpitTotalLabel").text()
        assert "nan" not in total_label.lower()
        assert "inf" not in total_label.lower()
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Deadline countdown
# ---------------------------------------------------------------------------


def test_cockpit_deadline_countdown_text(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        future = datetime.now() + timedelta(hours=2, minutes=30, seconds=10)
        ws.set_deadline(future.isoformat())
        text = ws._countdown_label.text()
        # Tolerance: countdown rounds down to the nearest minute, and the
        # 1s timer can race with the fixture by a tick — accept "2h 29m"
        # or "2h 30m".
        assert ("2h 30m" in text) or ("2h 29m" in text), text
    finally:
        ws.deleteLater()


def test_cockpit_past_deadline_shows_past_due(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        past = datetime.now() - timedelta(minutes=5)
        ws.set_deadline(past.isoformat())
        assert "PAST DUE" in ws._countdown_label.text()
    finally:
        ws.deleteLater()


def test_cockpit_no_deadline_shows_blank_or_default(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    try:
        ws.set_deadline(None)
        assert ws._countdown_label.text() == ""
        # Setting then clearing also clears.
        ws.set_deadline((datetime.now() + timedelta(hours=1)).isoformat())
        assert ws._countdown_label.text() != ""
        ws.set_deadline(None)
        assert ws._countdown_label.text() == ""
    finally:
        ws.deleteLater()


# ---------------------------------------------------------------------------
# Persistence — exclusions + sub-bids
# ---------------------------------------------------------------------------


def test_cockpit_exclusions_persist_to_json(qapp, tmp_path) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    fingerprint = "alpha.pdf:1024"
    text = "Insurance\nBond\nWinter conditions"

    ws = CockpitWorkspace(cache_dir=tmp_path)
    try:
        ws.set_pdf_fingerprint(fingerprint)
        ws._exclusions_edit.setPlainText(text)
        # textChanged → save fires; verify the JSON on disk.
        store_path = tmp_path / "cockpit.json"
        assert store_path.exists()
        blob = json.loads(store_path.read_text())
        assert blob[fingerprint]["exclusions"] == text
    finally:
        ws.deleteLater()

    # Recreate and confirm restore.
    ws2 = CockpitWorkspace(cache_dir=tmp_path)
    try:
        ws2.set_pdf_fingerprint(fingerprint)
        assert ws2._exclusions_edit.toPlainText() == text
    finally:
        ws2.deleteLater()


def test_cockpit_sub_bid_persist_to_json(qapp, tmp_path) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    fingerprint = "beta.pdf:2048"
    ws = CockpitWorkspace(cache_dir=tmp_path)
    try:
        ws.set_pdf_fingerprint(fingerprint)
        ws._append_sub_bid_row("Mechanical", "12500.00")
        ws._append_sub_bid_row("Electrical", "8750.50")
        # itemChanged → save fires from the appended rows; verify on-disk.
        store_path = tmp_path / "cockpit.json"
        assert store_path.exists()
        blob = json.loads(store_path.read_text())
        sub_bids = blob[fingerprint]["sub_bids"]
        trades = [entry[0] for entry in sub_bids]
        amounts = [entry[1] for entry in sub_bids]
        assert "Mechanical" in trades
        assert "12500.00" in amounts
    finally:
        ws.deleteLater()

    # Recreate and confirm restore.
    ws2 = CockpitWorkspace(cache_dir=tmp_path)
    try:
        ws2.set_pdf_fingerprint(fingerprint)
        assert ws2._sub_bids_table.rowCount() == 2
    finally:
        ws2.deleteLater()


# ---------------------------------------------------------------------------
# Regenerate proposal signal
# ---------------------------------------------------------------------------


def test_cockpit_regenerate_proposal_emits_signal(qapp) -> None:
    from ui.workspaces.cockpit_workspace import CockpitWorkspace

    ws = CockpitWorkspace()
    captured: list[bool] = []
    ws.proposal_export_requested.connect(lambda: captured.append(True))
    try:
        ws._regenerate_btn.click()
        assert captured == [True]
    finally:
        ws.deleteLater()
