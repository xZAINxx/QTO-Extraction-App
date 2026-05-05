"""Coverage aggregation — CSI division coverage + missing-sheet roster.

Mirrors ``ui/workspaces/coverage_workspace.py:83-240``. Surfaces:
  * Which CSI divisions have ZERO line items (the holes).
  * Which sheets the parser classified as productive but produced no
    rows (the silent skips).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from backend.db import QtoRow


# CSI MasterFormat divisions the QTO tool actively classifies into.
# Mirrors `config.yaml::csi_keywords` — kept in sync by hand for now.
_CSI_DIVISIONS: tuple[str, ...] = (
    "DIVISION 02",
    "DIVISION 03",
    "DIVISION 04",
    "DIVISION 05",
    "DIVISION 06",
    "DIVISION 07",
    "DIVISION 08",
    "DIVISION 09",
    "DIVISION 21",
    "DIVISION 22",
    "DIVISION 23",
    "DIVISION 26",
    "DIVISION 32",
)


def compute_coverage(
    rows: list[QtoRow],
    *,
    productive_sheets: list[str] | None = None,
) -> dict[str, Any]:
    """Pure aggregation — no DB access.

    ``productive_sheets`` is the set of sheet identifiers the parser
    classified as productive (PLAN_*, SCHEDULE, DETAIL_WITH_SCOPE).
    Sheets in this set with zero rows in ``rows`` are flagged as
    'silent skips' the user might want to investigate.
    """
    by_division: dict[str, int] = defaultdict(int)
    by_sheet: dict[str, int] = defaultdict(int)

    for r in rows:
        if r.is_header_row:
            continue
        div = (r.trade_division or "").strip()
        if div:
            by_division[div] += 1
        sheet = (r.source_sheet or "").strip()
        if sheet:
            by_sheet[sheet] += 1

    # Empty-division flag list — every CSI division NOT seen in `rows`.
    # Sorted by division number.
    empty_divisions = [
        d for d in _CSI_DIVISIONS if by_division.get(d, 0) == 0
    ]

    division_summary = [
        {"division": d, "row_count": by_division.get(d, 0)}
        for d in _CSI_DIVISIONS
    ]

    silent_skips: list[str] = []
    if productive_sheets:
        silent_skips = sorted(
            [s for s in productive_sheets if by_sheet.get(s, 0) == 0]
        )

    return {
        "division_summary": division_summary,
        "empty_divisions": empty_divisions,
        "silent_skips": silent_skips,
        "total_rows": sum(by_division.values()),
        "total_divisions_used": len(by_division),
        "total_divisions_available": len(_CSI_DIVISIONS),
    }


__all__ = ["compute_coverage", "_CSI_DIVISIONS"]
