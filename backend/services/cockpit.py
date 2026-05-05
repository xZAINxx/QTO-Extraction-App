"""Cockpit aggregation — division totals, markup math, sub-bid table.

Mirrors the desktop ``ui/workspaces/cockpit_workspace.py`` math: the
markup formula is ADDITIVE, not compound. ``base_total`` is the sum of
``qty * unit_price`` across non-header rows; the marked-up total
multiplies by ``(1 + (overhead + profit + contingency) / 100)``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.db import Project, QtoRow


def compute_cockpit(rows: list[QtoRow], project: Project) -> dict[str, Any]:
    """Pure aggregation function — no DB access, easy to unit-test."""
    base_total = 0.0
    division_totals: dict[str, float] = defaultdict(float)
    division_counts: dict[str, int] = defaultdict(int)
    sub_bid: list[dict[str, Any]] = []

    for r in rows:
        if r.is_header_row:
            continue
        unit = float(r.unit_price or 0)
        qty = float(r.qty or 0)
        line_total = qty * unit
        base_total += line_total
        div = (r.trade_division or "—").strip() or "—"
        division_totals[div] += line_total
        division_counts[div] += 1
        if unit > 0 and qty > 0:
            sub_bid.append({
                "description": r.description or "",
                "qty": qty,
                "units": r.units or "",
                "unit_price": unit,
                "total": line_total,
            })

    by_division = sorted(
        [
            {
                "division": div,
                "subtotal": subtotal,
                "row_count": division_counts[div],
            }
            for div, subtotal in division_totals.items()
        ],
        key=lambda d: d["subtotal"],
        reverse=True,
    )

    sub_bid.sort(key=lambda r: r["total"], reverse=True)
    sub_bid_top = sub_bid[:50]

    overhead = float(project.markup_overhead or 0)
    profit = float(project.markup_profit or 0)
    contingency = float(project.markup_contingency or 0)
    multiplier = 1 + (overhead + profit + contingency) / 100
    marked_up_total = base_total * multiplier

    return {
        "base_total": base_total,
        "by_division": by_division,
        "sub_bid": sub_bid_top,
        "sub_bid_truncated": len(sub_bid) > len(sub_bid_top),
        "sub_bid_total_count": len(sub_bid),
        "markup": {
            "overhead": overhead,
            "profit": profit,
            "contingency": contingency,
        },
        "marked_up_total": marked_up_total,
        "exclusions": list(project.exclusions or []),
        "project_name": project.name,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "row_count": sum(division_counts.values()),
    }


__all__ = ["compute_cockpit"]
