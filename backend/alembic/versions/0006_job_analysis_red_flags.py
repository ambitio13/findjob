"""add red_flags summary column to job_analyses

Revision ID: 0006_job_analysis_red_flags
Revises: 0005_external_idempotency
Create Date: 2026-08-11

Phase 3 job-risk lens: persist a sanitized red-flag summary on
``job_analyses`` so the job list can render risk labels and filter risky
jobs without re-parsing ``GeneratedArtifact.content``.

The column stores ``[{"flag_type", "title", "severity"}]`` only — evidence
quotes stay on the artifact (detail view). ``NULL`` means "no red flags
detected" so filtering stays dialect-neutral (``IS NOT NULL``).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_job_analysis_red_flags"
down_revision: str | None = "0005_external_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_analyses",
        sa.Column("red_flags", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_analyses", "red_flags")
