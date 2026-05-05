"""pdfs.page_classifications JSONB cache.

Revision ID: 0003_pdf_page_classifications
Revises: 0002_annotations
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_pdf_page_classifications"
down_revision: Union[str, Sequence[str], None] = "0002_annotations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pdfs",
        sa.Column(
            "page_classifications",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pdfs", "page_classifications")
