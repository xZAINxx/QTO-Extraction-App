"""Smoke tests for the SheetRail panel (Wave 2 — commit 4)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Force Qt to its offscreen platform so the suite stays headless on CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication — matches the convention used elsewhere."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Helpers — fake PDFs + metadata builders.
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path: Path, name: str = "test.pdf", payload: bytes = b"%PDF-1.4 test\n") -> Path:
    """Write a placeholder PDF so ``_fingerprint`` (which calls ``stat()``) works."""
    pdf = tmp_path / name
    pdf.write_bytes(payload)
    return pdf


def _fake_fitz_doc(page_count: int):
    """Build a ``fitz.open``-compatible MagicMock with a known page count."""
    doc = MagicMock()
    doc.page_count = page_count
    doc.__getitem__.side_effect = lambda i: MagicMock(name=f"page-{i}")
    doc.close = MagicMock()
    return doc


def _meta(page_num: int, sheet_number: str = "", sheet_title: str = ""):
    from ui.panels.sheet_rail import _SheetMeta
    return _SheetMeta(
        page_num=page_num, sheet_number=sheet_number, sheet_title=sheet_title,
    )


# ---------------------------------------------------------------------------
# Construction + sizing.
# ---------------------------------------------------------------------------


def test_sheet_rail_construction(qapp, tmp_path: Path) -> None:
    from ui.panels.sheet_rail import SheetRail
    rail = SheetRail(cache_dir=tmp_path)
    assert rail.isHidden()  # never shown by the test, must report hidden
    assert rail.sizeHint().width() == 220


def test_sheet_rail_collapsed_width_64(qapp, tmp_path: Path) -> None:
    from ui.panels.sheet_rail import SheetRail
    rail = SheetRail(cache_dir=tmp_path)
    rail.set_collapsed(True)
    assert rail.sizeHint().width() == 64
    rail.set_collapsed(False)
    assert rail.sizeHint().width() == 220


# ---------------------------------------------------------------------------
# Pure-logic discipline extraction (no widgets needed).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sheet_number,expected",
    [
        ("A-101", "A"),
        ("M-201", "M"),
        ("S-001", "S"),
        ("S5.1", "S"),
        ("E-401", "E"),
        ("P-100", "P"),
        ("C-002", "C"),
        ("L-501", "L"),
        ("a-101", "A"),  # case-insensitive
        ("X-999", "?"),  # unknown discipline
        ("", "?"),       # empty
        ("   ", "?"),    # whitespace
    ],
)
def test_sheet_rail_extracts_discipline_letter_from_sheet_number(
    sheet_number: str, expected: str,
) -> None:
    from ui.panels.sheet_rail import _discipline_from_sheet_number
    assert _discipline_from_sheet_number(sheet_number) == expected


def test_sheet_rail_falls_back_to_page_label_when_no_sheet_number(
    qapp, tmp_path: Path,
) -> None:
    from ui.panels.sheet_rail import SheetRail
    rail = SheetRail(cache_dir=tmp_path)
    rail._populate_from_metadata([_meta(7)])
    row = rail._rows[0]
    assert row.meta.label() == "Page 7"
    assert row._number_label.text() == "Page 7"


# ---------------------------------------------------------------------------
# Search + discipline filter.
# ---------------------------------------------------------------------------


def test_sheet_rail_search_filters_visible_rows(qapp, tmp_path: Path) -> None:
    from ui.panels.sheet_rail import SheetRail
    rail = SheetRail(cache_dir=tmp_path)
    rail._populate_from_metadata([
        _meta(1, "A-101", "First Floor Plan"),
        _meta(2, "A-102", "Second Floor Plan"),
        _meta(3, "M-201", "Mechanical Plan"),
    ])
    rail.show()  # rows must be visible by default to test the filter

    rail.search("A-101")
    visible = [r for r in rail._rows if r.isVisibleTo(rail)]
    assert len(visible) == 1
    assert visible[0].meta.sheet_number == "A-101"

    rail.search("")  # cleared filter — all visible again
    assert sum(1 for r in rail._rows if r.isVisibleTo(rail)) == 3


def test_sheet_rail_filter_by_discipline_filters_visible_rows(
    qapp, tmp_path: Path,
) -> None:
    from ui.panels.sheet_rail import SheetRail
    rail = SheetRail(cache_dir=tmp_path)
    rail._populate_from_metadata([
        _meta(1, "A-101", "Plan A"),
        _meta(2, "A-102", "Plan A2"),
        _meta(3, "M-201", "Mech"),
        _meta(4, "S-301", "Struct"),
    ])
    rail.show()
    rail.filter_by_discipline("A")
    visible = [r for r in rail._rows if r.isVisibleTo(rail)]
    assert len(visible) == 2
    assert all(r.meta.discipline == "A" for r in visible)
    rail.filter_by_discipline(None)
    assert sum(1 for r in rail._rows if r.isVisibleTo(rail)) == 4


# ---------------------------------------------------------------------------
# Scope persistence.
# ---------------------------------------------------------------------------


def test_sheet_rail_scope_persistence_roundtrip(qapp, tmp_path: Path) -> None:
    from ui.panels.sheet_rail import SheetRail

    pdf = _make_pdf(tmp_path)

    with patch("ui.panels.sheet_rail.fitz") as fake_fitz:
        fake_fitz.open.return_value = _fake_fitz_doc(page_count=3)
        rail = SheetRail(cache_dir=tmp_path)
        rail.load_pdf(str(pdf))
        # Mutate row #1 to "deferred" — should persist immediately.
        rail._rows[0].set_scope("deferred")

    # Fresh instance loads the same file and finds the persisted state.
    with patch("ui.panels.sheet_rail.fitz") as fake_fitz:
        fake_fitz.open.return_value = _fake_fitz_doc(page_count=3)
        rail2 = SheetRail(cache_dir=tmp_path)
        rail2.load_pdf(str(pdf))
        assert rail2._rows[0].scope == "deferred"
        assert rail2._rows[1].scope == "in"  # untouched, default
        assert rail2._rows[2].scope == "in"


def test_sheet_rail_scope_persistence_uses_pdf_fingerprint_key(
    qapp, tmp_path: Path,
) -> None:
    from ui.panels.sheet_rail import SheetRail, _fingerprint

    pdf = _make_pdf(tmp_path, "drawings.pdf")
    expected_key = _fingerprint(str(pdf))
    assert ":" in expected_key  # filename:filesize

    with patch("ui.panels.sheet_rail.fitz") as fake_fitz:
        fake_fitz.open.return_value = _fake_fitz_doc(page_count=2)
        rail = SheetRail(cache_dir=tmp_path)
        rail.load_pdf(str(pdf))
        rail._rows[1].set_scope("done")

    blob = json.loads((tmp_path / "scope.json").read_text())
    assert expected_key in blob
    assert blob[expected_key] == {"2": "done"}


# ---------------------------------------------------------------------------
# Click + signal wiring.
# ---------------------------------------------------------------------------


def test_sheet_rail_emits_sheet_clicked_with_page_num(qapp, tmp_path: Path) -> None:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QSignalSpy, QTest
    from ui.panels.sheet_rail import SheetRail

    rail = SheetRail(cache_dir=tmp_path)
    rail._populate_from_metadata([_meta(1, "A-101"), _meta(2, "A-102")])
    rail.resize(220, 600)
    rail.show()

    spy = QSignalSpy(rail.sheet_clicked)
    QTest.mouseClick(rail._rows[1], Qt.MouseButton.LeftButton)
    assert len(spy) == 1
    assert spy[0][0] == 2


def test_sheet_rail_set_active_marks_one_row(qapp, tmp_path: Path) -> None:
    from ui.panels.sheet_rail import SheetRail
    rail = SheetRail(cache_dir=tmp_path)
    rail._populate_from_metadata([_meta(1, "A-101"), _meta(2, "A-102")])
    rail.set_active_sheet(2)
    assert rail._rows[0].property("active") is False
    assert rail._rows[1].property("active") is True
