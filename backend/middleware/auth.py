"""Supabase JWT auth middleware + ``current_user`` dependency.

The Supabase frontend sends short-lived access tokens as
``Authorization: Bearer <jwt>`` on every ``/api/*`` request. This module
provides two pieces:

* :class:`SupabaseAuthMiddleware` — a Starlette ``BaseHTTPMiddleware``
  that intercepts each request, verifies the JWT against the Supabase
  project's HMAC secret, and stashes the decoded ``sub`` (user UUID) and
  ``email`` claims onto ``request.state``. Health / SPA / docs paths
  pass through unchecked so a logged-out browser can still load
  ``index.html`` and the boot bundle.

* :func:`current_user` — a FastAPI ``Depends``-able coroutine that turns
  the per-request user UUID into the application-side :class:`User`
  ORM row, lazy-provisioning a new row on first sight. Routes that need
  the authenticated user write
  ``user: Annotated[User, Depends(current_user)]`` and get back a
  hydrated model with the per-user prefs already loaded.

The middleware deliberately runs *after* CORS — preflight ``OPTIONS``
requests must be answered without an ``Authorization`` header for
browser fetch calls to succeed.
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from backend.config import get_settings
from backend.db import User, get_db


logger = logging.getLogger(__name__)


# Paths that bypass the auth middleware entirely. These either serve the
# SPA shell (so an unauthenticated browser can boot, hit /login, and grab
# a token) or are intentionally public — health probes, the static
# extraction-mode catalogue, and the FastAPI docs surface.
_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/api/info",
        "/api/extraction-modes",
        "/",
        "/index.html",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)
_PUBLIC_PREFIXES: tuple[str, ...] = ("/assets/",)


def _is_public_path(path: str) -> bool:
    """Return True when ``path`` should bypass JWT verification."""
    if path in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def _unauthorized(detail: str) -> JSONResponse:
    """Build a uniform 401 JSON response.

    Centralised so the wire format stays in lock-step with what the
    React client expects (a single ``detail`` field).
    """
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": detail},
    )


class SupabaseAuthMiddleware(BaseHTTPMiddleware):
    """Verify Supabase JWTs on every protected ``/api/*`` request.

    On success, the decoded ``sub`` claim (parsed as :class:`UUID`) is
    stored on ``request.state.user_id`` and the optional ``email`` claim
    on ``request.state.user_email``. Routes downstream pull these via
    the :func:`current_user` dependency rather than touching
    ``request.state`` directly.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path

        if _is_public_path(path):
            return await call_next(request)

        # Dev-mode short-circuit: when ``SUPABASE_JWT_SECRET`` is empty,
        # the operator hasn't configured Supabase yet (fresh checkout).
        # Mirror the frontend's ``isSupabaseConfigured=false`` posture:
        # let the request through with no user context so demo flows work
        # end-to-end. ``current_user`` will 401 if a route actually needs
        # auth — this only relaxes the middleware-level gate, not the
        # per-route requirement.
        if not self._settings.supabase_jwt_secret:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return _unauthorized("missing or invalid Authorization header")

        token = auth_header[len("bearer ") :].strip()
        if not token:
            return _unauthorized("missing or invalid Authorization header")

        try:
            payload = jwt.decode(
                token,
                self._settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except jwt.ExpiredSignatureError:
            return _unauthorized("token expired")
        except jwt.PyJWTError as exc:
            logger.info("auth: rejecting malformed token: %s", exc)
            return _unauthorized("invalid token")

        sub = payload.get("sub")
        if not sub:
            logger.info("auth: token missing 'sub' claim")
            return _unauthorized("invalid token")

        try:
            user_id = UUID(str(sub))
        except (ValueError, TypeError):
            logger.info("auth: 'sub' claim is not a UUID: %r", sub)
            return _unauthorized("invalid token")

        request.state.user_id = user_id
        request.state.user_email = payload.get("email")

        return await call_next(request)


# ── Dependencies ────────────────────────────────────────────────────────


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI dependency returning the authenticated :class:`User`.

    Reads the per-request UUID set by :class:`SupabaseAuthMiddleware`,
    looks up the matching ``users`` row, and lazy-creates one on first
    sight (keyed by Supabase's ``auth.users.id`` so RLS policies can
    use ``auth.uid()`` directly without a join).

    Usage::

        @router.get("/me")
        async def me(user: Annotated[User, Depends(current_user)]) -> dict:
            return {"id": str(user.id), "email": user.email}
    """
    user_id: UUID | None = getattr(request.state, "user_id", None)
    if user_id is None:
        # Defensive — the middleware should have rejected this request
        # already. Reaching here means a route was mounted on a public
        # path or the middleware was bypassed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )

    user_email: str | None = getattr(request.state, "user_email", None)

    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user is not None:
            return user

        # First time we've seen this Supabase user — create the app-side
        # row. The defaults on :class:`User` (extraction_mode='multi_agent',
        # cost_saver_mode=False) match the desktop app's config baseline.
        user = User(id=user_id, email=user_email)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("auth: provisioned new user id=%s email=%r", user_id, user_email)
        return user
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("auth: database error while loading user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to load user",
        ) from exc


__all__ = ["SupabaseAuthMiddleware", "current_user"]
