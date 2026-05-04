"""SQLAlchemy 2.0 models — Postgres only (Supabase target).

The schema mirrors the DDL in ``Plans/i-am-thinking-of-dapper-pebble.md``.
Postgres-specific types (``UUID``, ``JSONB``, ``ARRAY``) are used
deliberately — Supabase Postgres is the canonical target, and the
desktop app continues to use its own SQLite cache for local state.

Conventions:
* Primary keys are ``UUID`` with ``server_default=uuid_generate_v4()``
  via Postgres's ``gen_random_uuid()`` (built-in since PG13). The
  ``users`` table is the lone exception — its ``id`` column is set by
  the auth middleware to match Supabase's ``auth.users.id`` so RLS
  policies on the storage bucket can use ``auth.uid()`` directly.
* Timestamps use ``TIMESTAMPTZ`` (timezone-aware) and default to ``now()``
  server-side so clocks of background workers don't drift the column.
* All foreign keys are ``ON DELETE CASCADE`` because owning a project
  implies owning every PDF + extraction beneath it; a user-delete should
  unwind the entire tree without orphans.

JSON-serialisability: ``QtoRow.bbox`` is ``JSONB`` because the field is
optional and arrays of mixed types don't round-trip well through Postgres
arrays. ``QtoRow.risk_flags`` is a Postgres ``TEXT[]`` because the values
are a finite enumeration we filter / index on.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base. All concrete models subclass this."""


# ── User ─────────────────────────────────────────────────────────────


class User(Base):
    """Application-side mirror of a Supabase Auth user.

    ``id`` is supplied by the auth middleware on first sight (lazy
    provisioning) using the ``sub`` claim of the verified Supabase JWT.
    No FK to the ``auth`` schema — Supabase manages that table; we just
    coexist via the shared UUID space.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)

    # Per-user prefs (mirrors the desktop app's config.yaml settings).
    extraction_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default="multi_agent"
    )
    cost_saver_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ── Relationships ────────────────────────────────────────────
    projects: Mapped[list["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"<User id={self.id} email={self.email!r}>"


# ── Project ──────────────────────────────────────────────────────────


class Project(Base):
    """A user's QTO project — usually one drawing set / bid.

    Markup defaults are stored on the project so they survive across
    extractions; the Cockpit workspace inherits them on first open.
    Exclusions live here too (they're project-scoped scope notes, not
    per-extraction).
    """

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Markup percentages (additive, not compound — matches the PyQt6
    # cockpit math: total = base * (1 + (oh + profit + contingency)/100)).
    markup_overhead: Mapped[float] = mapped_column(Float, server_default="10")
    markup_profit: Mapped[float] = mapped_column(Float, server_default="8")
    markup_contingency: Mapped[float] = mapped_column(Float, server_default="5")

    exclusions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="projects")
    pdfs: Mapped[list["Pdf"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} name={self.name!r}>"


# ── Pdf ──────────────────────────────────────────────────────────────


class Pdf(Base):
    """An uploaded drawing-set PDF.

    ``storage_key`` is the Storage backend's identifier — for
    SupabaseStorage it's ``{user_id}/{project_id}/{pdf_id}/source.pdf``;
    for LocalDiskStorage it's the same shape under the configured root.
    The ``fingerprint`` is ``"{filename}:{byte_size}"`` (matches the
    desktop app's ``ResultCache`` key) so repeat uploads of the same set
    can dedupe later.
    """

    __tablename__ = "pdfs"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="pdfs")
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="pdf", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Pdf id={self.id} filename={self.filename!r}>"


# ── Extraction ───────────────────────────────────────────────────────


class Extraction(Base):
    """A run of the multi-agent pipeline against one PDF.

    Status transitions: ``pending → running → (completed | failed |
    canceled)``. The ``extraction_mode`` is snapshotted at start time so
    later changes to ``users.extraction_mode`` don't retroactively
    rewrite history.
    """

    __tablename__ = "extractions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pdf_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pdfs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String, nullable=False, server_default="pending"
    )
    extraction_mode: Mapped[str] = mapped_column(String, nullable=False)
    cost_saver_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # Aggregated cost summary — kept on the Extraction row for cheap
    # listings; ``token_events`` holds the per-call append-only log for
    # audit + the live cost popover.
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    pdf: Mapped[Pdf] = relationship(back_populates="extractions")
    user: Mapped[User] = relationship(back_populates="extractions")
    rows: Mapped[list["QtoRow"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )
    token_events: Mapped[list["TokenEvent"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Extraction id={self.id} status={self.status}>"


# ── QtoRow ───────────────────────────────────────────────────────────


class QtoRow(Base):
    """Flattened ``core.qto_row.QTORow`` persisted per extraction.

    Field set + types match ``core/qto_row.py:4-44`` exactly so the
    extraction runner can map between the dataclass and the ORM with
    ``dataclasses.asdict()``. ``position`` is the row's global ordinal
    within the extraction (preserves the order the assembler emitted
    them, including any post-sort).
    """

    __tablename__ = "qto_rows"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    extraction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("extractions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── QTORow dataclass fields ─────────────────────────────────
    s_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tag: Mapped[str | None] = mapped_column(String, nullable=True)
    drawings: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    units: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_formula: Mapped[str | None] = mapped_column(String, nullable=True)
    math_trail: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_division: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # bbox: (x0, y0, x1, y1) — JSONB rather than ARRAY because it's
    # nullable and we never query into individual coordinates from SQL.
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    is_header_row: Mapped[bool] = mapped_column(Boolean, server_default="false")
    confirmed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    needs_review: Mapped[bool] = mapped_column(Boolean, server_default="false")

    # Risk-flag taxonomy is a fixed enumeration; ARRAY(TEXT) lets us
    # filter rows with ``ANY('volatile_material' = qto_rows.risk_flags)``
    # in dashboards.
    risk_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    extraction: Mapped[Extraction] = relationship(back_populates="rows")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QtoRow id={self.id} desc={self.description!r}>"


# ── TokenEvent ───────────────────────────────────────────────────────


class TokenEvent(Base):
    """Append-only log of TokenTracker emissions during an extraction.

    Drives both the live cost popover (server pulls aggregate-by-model
    on popover open; SSE streams deltas) and the per-extraction billing
    audit. Inserted from the extraction runner inside the worker thread;
    the FastAPI side never writes here.
    """

    __tablename__ = "token_events"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    extraction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("extractions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    extraction: Mapped[Extraction] = relationship(back_populates="token_events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TokenEvent ext={self.extraction_id} model={self.model}>"


__all__ = [
    "Base",
    "User",
    "Project",
    "Pdf",
    "Extraction",
    "QtoRow",
    "TokenEvent",
]
