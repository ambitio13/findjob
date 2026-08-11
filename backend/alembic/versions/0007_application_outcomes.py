"""add application_outcomes table

Revision ID: 0007_application_outcomes
Revises: 0006_auth_users
Create Date: 2026-08-11

Adds the append-only outcome-event table closing the application feedback
loop (Phase 1). Each row records one observed outcome (replied / rejected /
interview / offer) with its source (manual / userscript_observed) and an
optional short sanitized evidence summary — never chat content or platform
session data.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_application_outcomes"
down_revision: str | None = "0006_auth_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_outcomes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("application_records.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("outcome_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_application_outcomes_application_id",
        "application_outcomes",
        ["application_id"],
    )
    op.create_index("ix_application_outcomes_user_id", "application_outcomes", ["user_id"])
    op.create_index(
        "ix_application_outcomes_outcome_type", "application_outcomes", ["outcome_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_application_outcomes_outcome_type", table_name="application_outcomes")
    op.drop_index("ix_application_outcomes_user_id", table_name="application_outcomes")
    op.drop_index("ix_application_outcomes_application_id", table_name="application_outcomes")
    op.drop_table("application_outcomes")
