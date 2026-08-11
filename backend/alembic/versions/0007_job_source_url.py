"""add source_url column to job_postings

Revision ID: 0007_job_source_url
Revises: 0006_job_analysis_red_flags
Create Date: 2026-08-11

Phase 4 multi-platform job entry: users paste a JD link together with the JD
text from *any* platform. The link is stored as provenance metadata on the
``job_postings`` row; the backend never fetches it.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_job_source_url"
down_revision: str | None = "0006_job_analysis_red_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("source_url", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_postings", "source_url")
