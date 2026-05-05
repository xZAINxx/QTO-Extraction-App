"""Spreadsheet export — wraps ``core/xlsx_exporter.py``.

The desktop app ships a heavy GC-estimate template
(``ESTIMATE_FORMAT___GC.xlsx``) and an exporter that fills it with
QTO rows + project metadata. We reuse that machinery verbatim and
serve the resulting workbook over HTTP.

Filters mirror the rows-API filter chips:
    ?division=<CSI div>  ?source_sheet=<sheet>  ?confirmed_only=true
so a user can export a focused slice (e.g. just "DIVISION 04 Masonry"
rows that are confirmed) — answers the user's "export the selected
group" ask.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import Extraction, Pdf, Project, QtoRow, User, get_db
from backend.middleware.auth import current_user


logger = logging.getLogger(__name__)

router = APIRouter(tags=["exports"])

# Repo root + GC estimate template — same one the desktop app uses.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO_ROOT / "ESTIMATE_FORMAT___GC.xlsx"


@router.get("/api/extractions/{extraction_id}/export.xlsx")
async def export_extraction_xlsx(
    extraction_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    division: str | None = Query(None, description="Filter to one CSI division"),
    source_sheet: str | None = Query(None, description="Filter to one sheet"),
    confirmed_only: bool = Query(False, description="Only export confirmed rows"),
    page_type: str | None = Query(
        None,
        description=(
            "Filter to rows from a specific page-type "
            "(PLAN_DEMO / PLAN_CONSTRUCTION / SCHEDULE / DETAIL_WITH_SCOPE / etc) — "
            "matched via Pdf.page_classifications cache."
        ),
    ),
) -> FileResponse:
    """Generate an XLSX from the GC template and stream it back.

    Owner-scoping resolves through Extraction → Pdf → Project.user_id.
    The export honours filter chips so the user can ship a slice
    instead of the full takeoff.
    """
    if not _TEMPLATE.is_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "ESTIMATE_FORMAT___GC.xlsx template missing — "
                "the desktop bundles it; verify it's checked into "
                "the repo root."
            ),
        )

    # Resolve extraction + parent project + owning PDF in one round-trip.
    result = await db.execute(
        select(Extraction, Pdf, Project)
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
    extraction, pdf, project = row

    # Load rows with filters.
    q = select(QtoRow).where(QtoRow.extraction_id == extraction_id)
    if division:
        q = q.where(QtoRow.trade_division == division)
    if source_sheet:
        q = q.where(QtoRow.source_sheet == source_sheet)
    if confirmed_only:
        q = q.where(QtoRow.confirmed.is_(True))
    q = q.order_by(asc(QtoRow.position))
    rows_result = await db.execute(q)
    db_rows = list(rows_result.scalars().all())

    # Filter by page-type via the pdfs.page_classifications cache.
    if page_type:
        page_classifications = pdf.page_classifications or {}
        allowed_pages = {
            int(p) for p, info in page_classifications.items()
            if info.get("page_type") == page_type
        }
        db_rows = [r for r in db_rows if r.source_page in allowed_pages]

    if not db_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no rows match the given filters",
        )

    # Adapt DB rows to the dataclass shape ``xlsx_exporter.export``
    # expects (it imports ``core.qto_row.QTORow``).
    domain_rows = await asyncio.to_thread(_to_domain_rows, db_rows)

    # Project meta — feeds the template's row 2-6 (PROJECT, BID DATE, etc).
    meta = {
        "project_name": project.name,
        "bid_date": (
            project.deadline.strftime("%Y-%m-%d") if project.deadline else ""
        ),
        "description": f"QTO export for {pdf.filename}",
        "extracted_at": (
            extraction.finished_at or extraction.created_at
        ).strftime("%Y-%m-%d %H:%M UTC"),
    }

    # Run the heavy lifting on a thread (openpyxl + shutil + image
    # styles all block the event loop).
    output_dir = tempfile.mkdtemp(prefix="qto-xlsx-")
    pdf_stem = Path(pdf.filename).stem or "export"

    def _do_export() -> str:
        # Late import — exporter pulls in openpyxl which is heavy at
        # import time. Late ensures we only pay it on real exports.
        from core.xlsx_exporter import export as xlsx_export

        return xlsx_export(
            rows=domain_rows,
            template_path=str(_TEMPLATE),
            output_dir=output_dir,
            pdf_stem=pdf_stem,
            project_meta=meta,
        )

    out_path = await asyncio.to_thread(_do_export)
    download_name = (
        f"{pdf_stem}_QTO_{datetime.now().strftime('%Y%m%d')}.xlsx"
    )
    logger.info(
        "export: extraction=%s rows=%d -> %s",
        extraction_id, len(db_rows), out_path,
    )
    return FileResponse(
        out_path,
        filename=download_name,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


def _to_domain_rows(db_rows: list[QtoRow]) -> list:
    """Translate ORM rows to ``core.qto_row.QTORow`` dataclasses.

    The desktop exporter expects the dataclass shape (it does
    ``getattr(row, ...)``); the ORM shape is close but field names
    bbox/risk_flags differ slightly. We materialise dataclasses so
    nothing in core/ needs to know about SQLAlchemy.
    """
    from core.qto_row import QTORow

    out: list[QTORow] = []
    for r in db_rows:
        out.append(QTORow(
            s_no=r.s_no or 0,
            tag=r.tag or "",
            drawings=r.drawings or "",
            details=r.details or "",
            math_trail=r.math_trail or "",
            description=r.description or "",
            qty=float(r.qty or 0),
            units=r.units or "",
            unit_price=float(r.unit_price or 0),
            total_formula=r.total_formula or "",
            trade_division=r.trade_division or "",
            is_header_row=bool(r.is_header_row),
            source_page=r.source_page or 0,
            source_sheet=r.source_sheet or "",
            extraction_method=r.extraction_method or "",
            confidence=float(r.confidence or 0),
            needs_review=bool(r.needs_review),
            bbox=tuple(r.bbox) if r.bbox else None,
            confirmed=bool(r.confirmed),
            risk_flags=list(r.risk_flags or []),
        ))
    return out


__all__ = ["router"]
