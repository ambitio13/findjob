"""add application_actions table for approval boundary

Revision ID: 0004_application_actions
Revises: 0003_app_records_ext
Create Date: 2026-08-01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_application_actions"
down_revision: str | None = "0003_app_records_ext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Normalized table for planned external actions + their approval boundary.
    # Per design.md an action binds the exact payload hash and source snapshot
    # at preview time; approval records who/when/which-hash was approved. No
    # secrets, cookies, tokens, or browser/session data live here.
    op.create_table(
        "application_actions",
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
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="approval_required"),
        sa.Column("payload_preview", sa.JSON, nullable=False),
        sa.Column("payload_hash", sa.String(80), nullable=False),
        sa.Column("source_snapshot", sa.JSON, nullable=False),
        sa.Column("approval", sa.JSON, nullable=True),
        sa.Column("stale_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_application_actions_application_id",
        "application_actions",
        ["application_id"],
    )
    op.create_index(
        "ix_application_actions_user_id",
        "application_actions",
        ["user_id"],
    )
    op.create_index(
        "ix_application_actions_action_type",
        "application_actions",
        ["action_type"],
    )
    op.create_index(
        "ix_application_actions_status",
        "application_actions",
        ["status"],
    )
    op.create_index(
        "ix_application_actions_payload_hash",
        "application_actions",
        ["payload_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_application_actions_payload_hash", table_name="application_actions")
    op.drop_index("ix_application_actions_status", table_name="application_actions")
    op.drop_index("ix_application_actions_action_type", table_name="application_actions")
    op.drop_index("ix_application_actions_user_id", table_name="application_actions")
    op.drop_index("ix_application_actions_application_id", table_name="application_actions")
    op.drop_table("application_actions")
