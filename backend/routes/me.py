"""``/api/me`` — authenticated user profile endpoint.

The SPA hits this once on first paint (after Supabase has handed it a
session token) to populate the topbar avatar/name and pull the per-user
extraction mode + cost-saver toggle. The route itself is trivially
``return user``; the heavy lifting — JWT verification, lazy provisioning
of the application-side ``users`` row — happens in the
:func:`backend.middleware.auth.current_user` dependency.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.db import User
from backend.middleware.auth import current_user


router = APIRouter(prefix="/api/me", tags=["me"])


class MeResponse(BaseModel):
    """Wire format for ``GET /api/me``.

    Fields mirror the persisted :class:`backend.db.User` columns the SPA
    actually consumes. ``created_at`` is serialised as an ISO 8601
    string so the client can feed it straight into ``new Date(...)``.
    """

    id: str
    email: str | None
    extraction_mode: str
    cost_saver_mode: bool
    created_at: str


@router.get("", response_model=MeResponse, summary="Authenticated user profile")
async def get_me(user: Annotated[User, Depends(current_user)]) -> MeResponse:
    """Return the authenticated user's profile.

    Uses the empty path (``""``) so the resolved URL is exactly
    ``/api/me`` rather than ``/api/me/`` — matches the desktop app's
    URL-without-trailing-slash convention.
    """
    created_at: datetime = user.created_at
    return MeResponse(
        id=str(user.id),
        email=user.email,
        extraction_mode=user.extraction_mode,
        cost_saver_mode=user.cost_saver_mode,
        created_at=created_at.isoformat(),
    )


__all__ = ["router", "MeResponse"]
