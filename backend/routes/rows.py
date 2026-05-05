"""Row-level routes — paginated read + targeted PATCH.

Pagination uses ``position``-cursor semantics (each row's stable
``position`` field within the extraction). Cursor-based pagination
beats offset on big result sets because the SQL planner can use the
``(extraction_id, position)`` index for a range scan without count-
all-the-way-back overhead.

PATCH is intentionally narrow — only the four fields the table cells
need to flip from the UI: ``confirmed`` (yellow stamp), ``needs_review``,
``description`` (the cell-edit affordance from the desktop app), and
``unit_price`` (manual override of an AI-extracted unit price).
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import Extraction, Pdf, Project, QtoRow, User, get_db
from backend.middleware.auth import current_user


logger = logging.getLogger(__name__)

router = APIRouter(tags=["rows"])

PAGE_LIMIT_DEFAULT = 200
PAGE_LIMIT_MAX = 1000


# ── Schemas ─────────────────────────────────────────────────────────


class RowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    extraction_id: UUID
    position: int
    s_no: int | None = None
    tag: str | None = None
    drawings: str | None = None
    details: str | None = None
    description: str | None = None
    qty: float | None = None
    units: str | None = None
    unit_price: float | None = None
    total_formula: str | None = None
    math_trail: str | None = None
    trade_division: str | None = None
    source_page: int | None = None
    source_sheet: str | None = None
    extraction_method: str | None = None
    confidence: float | None = None
    bbox: list[float] | None = None
    is_header_row: bool = False
    confirmed: bool = False
    needs_review: bool = False
    risk_flags: list[str] = []


class RowPage(BaseModel):
    rows: list[RowOut]
    next_cursor: int | None
    total: int


class RowPatch(BaseModel):
    """Partial update — only the four fields the UI flips per row."""

    confirmed: bool | None = None
    needs_review: bool | None = None
    description: str | None = None
    unit_price: float | None = None


# ── Helpers ─────────────────────────────────────────────────────────


async def _verify_extraction_owned(
    extraction_id: UUID, user: User, db: AsyncSession,
) -> None:
    """404 unless the extraction's owning project belongs to ``user``."""
    result = await db.execute(
        select(Extraction)
        .where(
            Extraction.id == extraction_id,
            Extraction.user_id == user.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="extraction not found",
        )


# ── Routes ──────────────────────────────────────────────────────────


@router.get(
    "/api/extractions/{extraction_id}/rows",
    response_model=RowPage,
)
async def list_extraction_rows(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: int = Query(0, ge=0, description="position to start AFTER"),
    limit: int = Query(PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX),
    trade_division: str | None = Query(None),
    source_sheet: str | None = Query(None),
    needs_review: bool | None = Query(None),
    confirmed: bool | None = Query(None),
) -> RowPage:
    """Paginated row read with optional filter chips.

    ``cursor`` is the position to start AFTER (exclusive). The first
    page passes ``cursor=0``. Subsequent pages pass the ``next_cursor``
    returned by the previous response. ``next_cursor`` is null when no
    more rows exist.

    All filters are AND-combined. The ``total`` count is unfiltered
    (drives the "showing N of M" caption when filters narrow the view).
    """
    await _verify_extraction_owned(extraction_id, user, db)

    base_q = select(QtoRow).where(QtoRow.extraction_id == extraction_id)
    filtered = base_q
    if trade_division is not None:
        filtered = filtered.where(QtoRow.trade_division == trade_division)
    if source_sheet is not None:
        filtered = filtered.where(QtoRow.source_sheet == source_sheet)
    if needs_review is not None:
        filtered = filtered.where(QtoRow.needs_review == needs_review)
    if confirmed is not None:
        filtered = filtered.where(QtoRow.confirmed == confirmed)

    page_q = (
        filtered
        .where(QtoRow.position > cursor)
        .order_by(asc(QtoRow.position))
        .limit(limit + 1)  # one extra to detect "is there a next page"
    )
    result = await db.execute(page_q)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = page_rows[-1].position if has_more and page_rows else None

    # Total — unfiltered count.  COUNT(*) is fine here; for huge tables
    # we'd switch to an estimate, but typical extractions are <10k rows.
    total_q = select(QtoRow.id).where(QtoRow.extraction_id == extraction_id)
    total_result = await db.execute(total_q)
    total = len(total_result.scalars().all())

    return RowPage(
        rows=[RowOut.model_validate(r) for r in page_rows],
        next_cursor=next_cursor,
        total=total,
    )


@router.patch("/api/rows/{row_id}", response_model=RowOut)
async def patch_row(
    row_id: UUID,
    payload: RowPatch,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RowOut:
    """Update one row's confirmed / needs_review / description / unit_price.

    Owner-scoping goes through the chain QtoRow → Extraction → User.
    """
    result = await db.execute(
        select(QtoRow)
        .join(Extraction, Extraction.id == QtoRow.extraction_id)
        .where(QtoRow.id == row_id, Extraction.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="row not found",
        )

    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return RowOut.model_validate(row)


__all__ = ["router"]
