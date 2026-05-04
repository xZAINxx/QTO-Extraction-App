"""Async SQLAlchemy engine + ``get_db`` FastAPI dependency.

Single engine per process, lazily constructed on first use. Tests can
swap the engine via ``init_engine(url=...)`` before any route handler
runs. Production calls ``init_engine()`` once at app startup with the
URL from ``Settings.database_url``.

The session factory is bound to the engine so every ``get_db`` yields
a fresh ``AsyncSession`` that auto-closes (and rolls back on uncaught
exceptions) when the request handler returns.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import get_settings


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None, *, echo: bool | None = None) -> AsyncEngine:
    """Build (or rebuild) the process-wide async engine.

    Idempotent for the same URL — subsequent calls with the same URL
    return the existing engine. Passing a different URL disposes the
    old engine before building the new one (used in test setup).
    """
    global _engine, _session_factory

    settings = get_settings()
    target_url = url or settings.database_url

    if _engine is not None:
        if str(_engine.url) == target_url:
            return _engine
        # URL changed (test override) — dispose and rebuild.
        # Fire-and-forget close; tests that care call dispose_engine().
        _engine.sync_engine.dispose()

    _engine = create_async_engine(
        target_url,
        echo=echo if echo is not None else settings.is_dev,
        pool_pre_ping=True,
        # Supabase's pgbouncer doesn't support session-level features;
        # NullPool sidesteps the issue and is correct for short-lived
        # FastAPI request handlers.
        pool_size=5,
        max_overflow=10,
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _engine


async def dispose_engine() -> None:
    """Tear down the engine (call from FastAPI shutdown handler)."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an ``AsyncSession`` per request.

    Auto-rolls-back on uncaught exceptions and always closes the session
    on exit. Routes commit explicitly; we deliberately don't auto-commit
    so failed-route side-effects aren't half-persisted.
    """
    if _session_factory is None:
        # Lazy-init for code paths that don't call ``init_engine`` first
        # (notably, scripts and one-off CLI tools).
        init_engine()
    assert _session_factory is not None  # narrows the type for mypy

    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


__all__ = ["init_engine", "dispose_engine", "get_db"]
