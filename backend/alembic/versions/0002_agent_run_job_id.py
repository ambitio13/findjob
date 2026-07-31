"""add agent_runs.job_id

Revision ID: 0002_agent_run_job_id
Revises: 0001_initial
Create Date: 2026-07-31

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_agent_run_job_id"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add nullable job_id so failed runs (which create no JobAnalysis row) can
    # still be scoped to a job. Demo runs leave this NULL.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    if "job_id" not in columns:
        op.add_column(
            "agent_runs",
            sa.Column("job_id", sa.String(64), nullable=True),
        )

    indexes = {index["name"] for index in inspector.get_indexes("agent_runs")}
    if "ix_agent_runs_job_id" not in indexes:
        op.create_index(
            "ix_agent_runs_job_id",
            "agent_runs",
            ["job_id"],
        )

    foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("agent_runs")
        if fk["constrained_columns"] == ["job_id"]
    }
    if "fk_agent_runs_job_id_job_postings" not in foreign_keys:
        op.create_foreign_key(
            "fk_agent_runs_job_id_job_postings",
            "agent_runs",
            "job_postings",
            ["job_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("agent_runs")
        if fk["constrained_columns"] == ["job_id"]
    }
    if "fk_agent_runs_job_id_job_postings" in foreign_keys:
        op.drop_constraint(
            "fk_agent_runs_job_id_job_postings",
            "agent_runs",
            type_="foreignkey",
        )

    indexes = {index["name"] for index in inspector.get_indexes("agent_runs")}
    if "ix_agent_runs_job_id" in indexes:
        op.drop_index("ix_agent_runs_job_id", table_name="agent_runs")

    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    if "job_id" in columns:
        op.drop_column("agent_runs", "job_id")
