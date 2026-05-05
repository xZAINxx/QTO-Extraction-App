"""annotations table — Phase 4 toolkit storage.

Revision ID: 0002_annotations
Revises: 0001_initial
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0002_annotations"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pdf_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pdfs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sheet_number", sa.String(), nullable=False),
        sa.Column("page_num", sa.Integer(), nullable=False),
        # type ∈ {highlight, cloud, callout, dimension, text_box, legend}
        sa.Column("type", sa.String(), nullable=False),
        # geometry shape varies per type — JSONB so we don't fight SQL.
        sa.Column("geometry", postgresql.JSONB(), nullable=False),
        # Defaults to confirmed-yellow so a fresh highlight is visible.
        sa.Column(
            "color",
            sa.String(),
            nullable=False,
            server_default="#FDE047",
        ),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "takeoff_row_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("qto_rows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_annotations_pdf_sheet",
        "annotations",
        ["pdf_id", "sheet_number"],
    )
    op.create_index(
        "ix_annotations_takeoff",
        "annotations",
        ["takeoff_row_id"],
        postgresql_where=sa.text("takeoff_row_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_annotations_takeoff", table_name="annotations")
    op.drop_index("ix_annotations_pdf_sheet", table_name="annotations")
    op.drop_table("annotations")
