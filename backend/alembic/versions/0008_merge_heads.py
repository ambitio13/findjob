"""merge outcomes and source_url branches

Revision ID: 0008_merge_heads
Revises: 0007_application_outcomes, 0007_job_source_url
Create Date: 2026-08-11

The Phase 1 outcome work (``0006_auth_users`` → ``0007_application_outcomes``)
and the Phase 4 multi-platform work (``0006_job_analysis_red_flags`` →
``0007_job_source_url``) were developed on parallel branches from
``0005_external_idempotency``. Both branches are independent (disjoint tables
and columns), so the merge is a no-op that only re-joins the revision graph.
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0008_merge_heads"
down_revision: str | Sequence[str] | None = (
    "0007_application_outcomes",
    "0007_job_source_url",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
