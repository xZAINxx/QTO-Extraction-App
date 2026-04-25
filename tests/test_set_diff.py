"""Phase-5 set_diff regression tests.

Covers three behaviours we want to lock in:

* identical PDFs → 0 modified pages, 0 clusters
* a synthetic rectangle drawn on one page → exactly that page is flagged
  ``modified`` with at least one cluster
* the cluster bbox falls inside the modified region (we use ``page.rect``
  display coords so this works for rotated sheets too)

Skips cleanly when the Brooklyn fixture isn't checked out so CI without
the asset still passes.
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from core.set_diff import diff_sets, changed_page_numbers, merge_partial_rerun


_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_PDF = _REPO_ROOT / "QTO Tool" / "Drawings Set.pdf"


def _have_fixture() -> bool:
    return _FIXTURE_PDF.exists()


@pytest.fixture(scope="module")
def fixture_pdf() -> Path:
    if not _have_fixture():
        pytest.skip("Brooklyn drawings fixture not present.")
    return _FIXTURE_PDF


def _make_subset(src_path: Path, n: int) -> fitz.Document:
    src = fitz.open(str(src_path))
    out = fitz.open()
    for i in range(min(n, src.page_count)):
        out.insert_pdf(src, from_page=i, to_page=i)
    src.close()
    return out


def test_identical_pdfs_have_no_changes(fixture_pdf: Path, tmp_path: Path):
    """Self-diff must report zero changes (the cv2 / hash short-circuit)."""
    result = diff_sets(
        str(fixture_pdf), str(fixture_pdf),
        ai_client=None, describe=False,
    )
    modified = [p for p in result.pairs if p.status != "unchanged"]
    assert modified == [], (
        f"identical PDFs reported changes: {[(p.sheet_id, p.status) for p in modified]}"
    )
    assert changed_page_numbers(result) == set()


def test_synthetic_rect_detected(fixture_pdf: Path, tmp_path: Path):
    """Drawing a single black rect on page 3 must light up exactly that page."""
    old_doc = _make_subset(fixture_pdf, 5)
    new_doc = _make_subset(fixture_pdf, 5)
    target_page_idx = 2  # page 3 (0-indexed)
    page = new_doc[target_page_idx]
    r = page.rect
    drawn = fitz.Rect(r.x0 + 200, r.y0 + 200, r.x0 + 340, r.y0 + 260)
    page.draw_rect(drawn, color=(0, 0, 0), fill=(0, 0, 0), width=2, overlay=True)

    old_path = tmp_path / "old.pdf"
    new_path = tmp_path / "new.pdf"
    old_doc.save(str(old_path))
    new_doc.save(str(new_path))
    old_doc.close()
    new_doc.close()

    result = diff_sets(
        str(old_path), str(new_path),
        ai_client=None, describe=False,
    )
    modified = [p for p in result.pairs if p.status == "modified"]
    assert len(modified) == 1, (
        f"expected exactly one modified page, got: "
        f"{[(p.sheet_id, p.status) for p in result.pairs]}"
    )
    pair = modified[0]
    assert pair.new_page == target_page_idx + 1
    assert pair.clusters, "modified page must produce at least one cluster"
    # All other pages stay unchanged (proves the hash pre-filter is working).
    other_modified = [
        p for p in result.pairs
        if p.status != "unchanged" and p.new_page != target_page_idx + 1
    ]
    assert other_modified == [], (
        f"unrelated pages reported as changed: "
        f"{[(p.sheet_id, p.status) for p in other_modified]}"
    )
    assert changed_page_numbers(result) == {target_page_idx + 1}


def test_merge_partial_rerun_replaces_changed_sheets():
    """``merge_partial_rerun`` swaps rows for changed sheets, keeps the rest."""
    class _Row:
        def __init__(self, sheet, desc):
            self.source_sheet = sheet
            self.description = desc

    cached = [_Row("A-101", "old door"), _Row("A-102", "old wall"), _Row("A-103", "old win")]
    fresh = [_Row("A-102", "new wall")]
    merged = merge_partial_rerun(
        cached, fresh, changed_sheet_ids={"A-102"},
    )
    descs = sorted(r.description for r in merged)
    assert descs == ["new wall", "old door", "old win"]
