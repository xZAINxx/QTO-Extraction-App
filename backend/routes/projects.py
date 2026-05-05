"""Project CRUD routes — owner-scoped via ``current_user``.

Every read / write filters by ``Project.user_id == current_user.id`` so
a forged path param can't expose another user's project. CASCADE on the
``user_id`` and ``project_id`` foreign keys handles fan-out: deleting a
project deletes its PDFs, extractions, and rows transactionally in
Postgres without an explicit traversal here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import Project, User, get_db
from backend.middleware.auth import current_user


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ── Schemas ─────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    deadline: datetime | None = None


class ProjectUpdate(BaseModel):
    """Partial update — every field optional; ``model_dump(exclude_unset=True)``
    only writes the keys the client actually sent."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    deadline: datetime | None = None
    markup_overhead: float | None = None
    markup_profit: float | None = None
    markup_contingency: float | None = None
    exclusions: list[str] | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    deadline: datetime | None = None
    markup_overhead: float
    markup_profit: float
    markup_contingency: float
    exclusions: list[str]
    created_at: datetime
    updated_at: datetime


# ── Helpers ─────────────────────────────────────────────────────────


async def _load_owned(
    project_id: UUID, user: User, db: AsyncSession
) -> Project:
    """Return the project iff the current user owns it; 404 otherwise.

    Same 404 for "doesn't exist" and "not yours" so we don't leak the
    existence of other users' projects.
    """
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


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ProjectOut]:
    """List the current user's projects, most-recently-updated first."""
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user.id)
        .order_by(Project.updated_at.desc())
    )
    return [ProjectOut.model_validate(p) for p in result.scalars().all()]


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: ProjectCreate,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectOut:
    project = Project(
        user_id=user.id,
        name=payload.name,
        deadline=payload.deadline,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    logger.info(
        "project: created id=%s name=%r user=%s",
        project.id, project.name, user.id,
    )
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectOut:
    project = await _load_owned(project_id, user, db)
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectOut:
    project = await _load_owned(project_id, user, db)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(project, key, value)
    await db.commit()
    await db.refresh(project)
    return ProjectOut.model_validate(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete the project. CASCADE handles pdfs / extractions / rows.

    Storage objects for any contained PDFs are NOT swept here — that's a
    separate sweeper concern (orphaned objects are recoverable from
    ``pdfs.storage_key``-shaped paths if needed). Documented as a TODO
    on the cleanup workstream.
    """
    project = await _load_owned(project_id, user, db)
    await db.delete(project)
    await db.commit()
    logger.info("project: deleted id=%s user=%s", project_id, user.id)


__all__ = ["router"]
