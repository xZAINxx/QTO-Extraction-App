"""Annotation CRUD — the persistence layer for the Phase 4 toolkit.

Owner-scoping goes through Pdf → Project → user_id, mirroring the
pattern the rest of the routes use. Geometry is stored as JSONB and
returned to the client as-is — the React canvas owns the rendering;
the backend doesn't interpret shape semantics.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import Annotation, Pdf, Project, User, get_db
from backend.middleware.auth import current_user


logger = logging.getLogger(__name__)

router = APIRouter(tags=["annotations"])


_VALID_TYPES = ("highlight", "cloud", "callout", "dimension", "text_box", "legend")


class AnnotationCreate(BaseModel):
    sheet_number: str
    page_num: int
    type: Literal["highlight", "cloud", "callout", "dimension", "text_box", "legend"]
    geometry: dict[str, Any]
    color: str = "#FDE047"
    label: str | None = None
    takeoff_row_id: UUID | None = None


class AnnotationUpdate(BaseModel):
    geometry: dict[str, Any] | None = None
    color: str | None = None
    label: str | None = None
    takeoff_row_id: UUID | None = None


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pdf_id: UUID
    sheet_number: str
    page_num: int
    type: str
    geometry: dict[str, Any]
    color: str
    label: str | None = None
    takeoff_row_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


async def _verify_pdf_owned(
    pdf_id: UUID, user: User, db: AsyncSession,
) -> None:
    result = await db.execute(
        select(Pdf)
        .join(Project, Project.id == Pdf.project_id)
        .where(Pdf.id == pdf_id, Project.user_id == user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="pdf not found",
        )


async def _load_owned_annotation(
    annotation_id: UUID, user: User, db: AsyncSession,
) -> Annotation:
    result = await db.execute(
        select(Annotation)
        .join(Pdf, Pdf.id == Annotation.pdf_id)
        .join(Project, Project.id == Pdf.project_id)
        .where(Annotation.id == annotation_id, Project.user_id == user.id)
    )
    annotation = result.scalar_one_or_none()
    if annotation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="annotation not found",
        )
    return annotation


@router.get("/api/pdfs/{pdf_id}/annotations", response_model=list[AnnotationOut])
async def list_annotations(
    pdf_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sheet_number: str | None = None,
) -> list[AnnotationOut]:
    """List all annotations for a PDF (or filter to one sheet)."""
    await _verify_pdf_owned(pdf_id, user, db)
    q = select(Annotation).where(Annotation.pdf_id == pdf_id)
    if sheet_number is not None:
        q = q.where(Annotation.sheet_number == sheet_number)
    q = q.order_by(asc(Annotation.created_at))
    result = await db.execute(q)
    return [AnnotationOut.model_validate(a) for a in result.scalars().all()]


@router.post(
    "/api/pdfs/{pdf_id}/annotations",
    response_model=AnnotationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    pdf_id: UUID,
    payload: AnnotationCreate,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnnotationOut:
    await _verify_pdf_owned(pdf_id, user, db)
    annotation = Annotation(
        pdf_id=pdf_id,
        user_id=user.id,
        sheet_number=payload.sheet_number,
        page_num=payload.page_num,
        type=payload.type,
        geometry=payload.geometry,
        color=payload.color,
        label=payload.label,
        takeoff_row_id=payload.takeoff_row_id,
    )
    db.add(annotation)
    await db.commit()
    await db.refresh(annotation)
    return AnnotationOut.model_validate(annotation)


@router.patch("/api/annotations/{annotation_id}", response_model=AnnotationOut)
async def update_annotation(
    annotation_id: UUID,
    payload: AnnotationUpdate,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnnotationOut:
    annotation = await _load_owned_annotation(annotation_id, user, db)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(annotation, key, value)
    await db.commit()
    await db.refresh(annotation)
    return AnnotationOut.model_validate(annotation)


@router.delete(
    "/api/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_annotation(
    annotation_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    annotation = await _load_owned_annotation(annotation_id, user, db)
    await db.delete(annotation)
    await db.commit()


__all__ = ["router"]
