"""Workspace-aggregate routes — Cockpit, Coverage, set-Diff.

Each route fetches an extraction's rows, calls a pure aggregation
function in ``backend/services/{cockpit,coverage}.py``, and returns
the JSON the React workspace consumes. Owner-scoped via the same
join chain other routes use (Extraction → Project.user_id).

Diff is a light wrapper for now — the real ``core/set_diff.py``
machinery requires both PDFs to be local; PR #2 ships the route
shape with a placeholder summary. The full diff lands as a follow-up
in PR #3 alongside the rest of the annotation toolkit.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import Extraction, Pdf, Project, QtoRow, User, get_db
from backend.middleware.auth import current_user
from backend.services.cockpit import compute_cockpit
from backend.services.coverage import compute_coverage


logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspaces"])


# ── Schemas ─────────────────────────────────────────────────────────


class DivisionRow(BaseModel):
    division: str
    subtotal: float
    row_count: int


class SubBidRow(BaseModel):
    description: str
    qty: float
    units: str
    unit_price: float
    total: float


class CockpitOut(BaseModel):
    base_total: float
    by_division: list[DivisionRow]
    sub_bid: list[SubBidRow]
    sub_bid_truncated: bool
    sub_bid_total_count: int
    markup: dict[str, float]
    marked_up_total: float
    exclusions: list[str]
    project_name: str
    deadline: str | None
    row_count: int


class CoverageDivision(BaseModel):
    division: str
    row_count: int


class CoverageOut(BaseModel):
    division_summary: list[CoverageDivision]
    empty_divisions: list[str]
    silent_skips: list[str]
    total_rows: int
    total_divisions_used: int
    total_divisions_available: int


class DiffOut(BaseModel):
    """Stub — full diff result lands in PR #3."""

    base_extraction_id: UUID
    compare_extraction_id: UUID
    summary: str
    note: str


# ── Helpers ─────────────────────────────────────────────────────────


async def _load_extraction_with_project(
    extraction_id: UUID, user: User, db: AsyncSession,
) -> tuple[Extraction, Project]:
    """Return (extraction, project) iff the user owns the extraction."""
    result = await db.execute(
        select(Extraction, Project)
        .join(Pdf, Pdf.id == Extraction.pdf_id)
        .join(Project, Project.id == Pdf.project_id)
        .where(
            Extraction.id == extraction_id,
            Project.user_id == user.id,
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="extraction not found",
        )
    return row


async def _load_rows(extraction_id: UUID, db: AsyncSession) -> list[QtoRow]:
    result = await db.execute(
        select(QtoRow)
        .where(QtoRow.extraction_id == extraction_id)
        .order_by(asc(QtoRow.position))
    )
    return list(result.scalars().all())


# ── Routes ──────────────────────────────────────────────────────────


@router.get(
    "/api/extractions/{extraction_id}/cockpit",
    response_model=CockpitOut,
)
async def get_cockpit(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CockpitOut:
    extraction, project = await _load_extraction_with_project(
        extraction_id, user, db,
    )
    rows = await _load_rows(extraction.id, db)
    payload = compute_cockpit(rows, project)
    return CockpitOut(**payload)


@router.get(
    "/api/extractions/{extraction_id}/coverage",
    response_model=CoverageOut,
)
async def get_coverage(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CoverageOut:
    extraction, _project = await _load_extraction_with_project(
        extraction_id, user, db,
    )
    rows = await _load_rows(extraction.id, db)
    payload = compute_coverage(rows)
    return CoverageOut(**payload)


@router.get(
    "/api/extractions/{base_id}/diff/{compare_id}",
    response_model=DiffOut,
)
async def get_diff(
    base_id: UUID,
    compare_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DiffOut:
    """Stub diff route — full implementation lives in PR #3.

    Verifies ownership of both extractions and returns a placeholder
    summary so the frontend's What-Changed workspace can render
    something now and hot-swap to real diff data later.
    """
    await _load_extraction_with_project(base_id, user, db)
    await _load_extraction_with_project(compare_id, user, db)
    return DiffOut(
        base_extraction_id=base_id,
        compare_extraction_id=compare_id,
        summary="Set-diff lands in PR #3 alongside the annotation toolkit.",
        note=(
            "PR #2 ships ownership verification + the route shape so "
            "the frontend can wire the workspace; the real diff result "
            "(changed pages, $ impact, sheet roster) needs the desktop's "
            "core/set_diff.py adapter, which depends on both PDFs being "
            "locally present at the same time."
        ),
    )


__all__ = ["router"]
