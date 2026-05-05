"""PDF upload + management routes.

Owner-scoping is enforced via JOIN to ``projects`` (so we never trust a
``pdf_id`` path param without proving the chain belongs to the current
user). The 50MB cap matches CPM's ``main.py`` import route; per-user
quota is configurable via ``Settings.per_user_storage_quota_mb`` and
defends the bucket against a single user filling it.

The blocking parts (storage put / get, ``fitz.open``) are wrapped in
``asyncio.to_thread`` per the contract documented in
``backend/services/storage.py`` — they must not run on the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import Pdf, Project, User, get_db
from backend.middleware.auth import current_user
from backend.services.storage import Storage, StorageError, get_storage


logger = logging.getLogger(__name__)

# Hard cap mirrors CPM. Anything bigger and the uvicorn worker spends
# minutes on a single upload — at which point we should switch to a
# resumable scheme (out of v1 scope).
_MAX_BYTES = 50 * 1024 * 1024

router = APIRouter(tags=["pdfs"])


# ── Schemas ─────────────────────────────────────────────────────────


class PdfOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    filename: str
    page_count: int | None = None
    byte_size: int
    fingerprint: str
    uploaded_at: datetime


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int


# ── Helpers ─────────────────────────────────────────────────────────


def _storage_key(user_id: UUID, project_id: UUID, pdf_id: UUID) -> str:
    """Bucket key shape — identical to the Plans/.../dapper-pebble.md spec.

    Encoding ``user_id`` as the leading folder lets the Supabase bucket's
    RLS policy use ``(storage.foldername(name))[1]`` to enforce per-user
    isolation; the SQL FK chain provides defence-in-depth.
    """
    return f"{user_id}/{project_id}/{pdf_id}/source.pdf"


def _count_pages(path: str) -> int:
    """Open the PDF on the threadpool, return page count, close."""
    doc = fitz.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


async def _load_pdf_owned(
    pdf_id: UUID, user: User, db: AsyncSession
) -> Pdf:
    """Return the PDF iff its owning project belongs to ``user``."""
    result = await db.execute(
        select(Pdf)
        .join(Project, Project.id == Pdf.project_id)
        .where(Pdf.id == pdf_id, Project.user_id == user.id)
    )
    pdf = result.scalar_one_or_none()
    if pdf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pdf not found",
        )
    return pdf


async def _verify_project_owned(
    project_id: UUID, user: User, db: AsyncSession
) -> Project:
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == user.id,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project not found",
        )
    return project


# ── Routes ──────────────────────────────────────────────────────────


@router.post(
    "/api/projects/{project_id}/pdfs",
    response_model=PdfOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_pdf(
    project_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
    file: UploadFile = File(...),
) -> PdfOut:
    """Upload a PDF into a project's storage prefix."""
    await _verify_project_owned(project_id, user, db)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="upload a .pdf file",
        )

    data = await file.read()
    byte_size = len(data)
    if byte_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty file",
        )
    if byte_size > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file too large ({byte_size / 1024 / 1024:.1f}MB; max 50MB)",
        )

    # Soft per-user quota: sum existing byte_size across the user's pdfs.
    settings = get_settings()
    quota_bytes = settings.per_user_storage_quota_mb * 1024 * 1024
    used_result = await db.execute(
        select(func.coalesce(func.sum(Pdf.byte_size), 0))
        .join(Project, Project.id == Pdf.project_id)
        .where(Project.user_id == user.id)
    )
    used = int(used_result.scalar_one() or 0)
    if used + byte_size > quota_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="storage quota exceeded",
        )

    pdf_id = uuid4()
    storage_key = _storage_key(user.id, project_id, pdf_id)
    fingerprint = f"{file.filename}:{byte_size}"

    try:
        await asyncio.to_thread(
            storage.put,
            storage_key,
            data,
            content_type="application/pdf",
        )
    except StorageError as exc:
        logger.exception("storage put failed for %s", storage_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="storage unavailable",
        ) from exc

    # Page count is best-effort. If PyMuPDF can't parse the file we
    # still keep the upload — the extraction worker will surface the
    # real error later. Cleanup of the storage object happens via the
    # DELETE route once the user removes the PDF.
    page_count: int | None = None
    try:
        with storage.local_path(storage_key) as local_path:
            page_count = await asyncio.to_thread(_count_pages, str(local_path))
    except Exception as exc:
        logger.warning(
            "pdf: page-count failed for %s: %s", storage_key, exc
        )

    pdf = Pdf(
        id=pdf_id,
        project_id=project_id,
        filename=file.filename,
        storage_key=storage_key,
        page_count=page_count,
        byte_size=byte_size,
        fingerprint=fingerprint,
    )
    db.add(pdf)
    await db.commit()
    await db.refresh(pdf)
    logger.info(
        "pdf: uploaded id=%s name=%r pages=%s size=%dB user=%s",
        pdf.id, pdf.filename, page_count, byte_size, user.id,
    )
    return PdfOut.model_validate(pdf)


@router.get(
    "/api/projects/{project_id}/pdfs",
    response_model=list[PdfOut],
)
async def list_project_pdfs(
    project_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PdfOut]:
    await _verify_project_owned(project_id, user, db)
    result = await db.execute(
        select(Pdf)
        .join(Project, Project.id == Pdf.project_id)
        .where(Project.id == project_id, Project.user_id == user.id)
        .order_by(Pdf.uploaded_at.desc())
    )
    return [PdfOut.model_validate(p) for p in result.scalars().all()]


@router.get("/api/pdfs/{pdf_id}", response_model=PdfOut)
async def get_pdf(
    pdf_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PdfOut:
    pdf = await _load_pdf_owned(pdf_id, user, db)
    return PdfOut.model_validate(pdf)


@router.delete(
    "/api/pdfs/{pdf_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_pdf(
    pdf_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> None:
    """Delete a PDF row + its storage object.

    DB row first, storage second: an orphaned storage object is
    recoverable (manual sweeper); an orphaned DB row pointing at a
    missing object is not. Storage delete failures are logged, not
    raised — the user's intent is satisfied either way.
    """
    pdf = await _load_pdf_owned(pdf_id, user, db)
    storage_key = pdf.storage_key
    await db.delete(pdf)
    await db.commit()
    try:
        await asyncio.to_thread(storage.delete, storage_key)
    except StorageError as exc:
        logger.warning(
            "pdf: storage delete failed for %s: %s", storage_key, exc
        )


@router.get("/api/pdfs/{pdf_id}/signed-url", response_model=SignedUrlOut)
async def get_pdf_signed_url(
    pdf_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> SignedUrlOut:
    """Return a time-limited URL the SPA's PDF viewer can fetch.

    LocalDiskStorage returns a relative ``/storage/{key}`` URL that
    only works while the dev server is up; SupabaseStorage returns a
    real signed URL good for ``expires_in`` seconds. Either is safe to
    hand to the React canvas — the URL itself encodes the access grant.
    """
    pdf = await _load_pdf_owned(pdf_id, user, db)
    expires_in = 3600
    try:
        url = await asyncio.to_thread(
            storage.signed_url, pdf.storage_key, expires_in=expires_in
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="signed-url generation failed",
        ) from exc
    return SignedUrlOut(url=url, expires_in=expires_in)


__all__ = ["router"]
