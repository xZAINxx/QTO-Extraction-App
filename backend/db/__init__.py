"""Database layer — SQLAlchemy 2.0 async models + session factory.

Public re-exports:
    Base                 — declarative base for all models (in ``models``).
    User, Project, Pdf, Extraction, QtoRow, TokenEvent
                         — model classes (commit 1).
    get_db               — FastAPI dependency yielding an ``AsyncSession``.
    init_engine          — call once at app startup; idempotent.
    dispose_engine       — call once at app shutdown; idempotent.

Migrations live alongside this package in ``backend/db/migrations/``;
Alembic drives them via ``alembic upgrade head`` in the entrypoint.
"""
from __future__ import annotations

from .models import (
    Annotation,
    Base,
    Extraction,
    Pdf,
    Project,
    QtoRow,
    TokenEvent,
    User,
)
from .session import (
    dispose_engine,
    get_db,
    get_sync_session_factory,
    init_engine,
)

__all__ = [
    "Annotation",
    "Base",
    "Extraction",
    "Pdf",
    "Project",
    "QtoRow",
    "TokenEvent",
    "User",
    "dispose_engine",
    "get_db",
    "get_sync_session_factory",
    "init_engine",
]
