"""add follow_up_suggestions and threshold_calibrations tables

Revision ID: 0009_followup_calibration
Revises: 0008_merge_heads
Create Date: 2026-08-11

Phase 5 feedback loop storage:

- ``follow_up_suggestions`` — daily-scan produced follow-up suggestions
  (e.g. 3 days without reply → change opening message). Suggestions are
  advisory only: acting on one always goes through the existing generation +
  approval boundary, so a suggestion row never triggers external effects.
- ``threshold_calibrations`` — append-only history of statistically derived
  ``COMMUNICATE_MIN_SCORE`` calibrations. The newest row per user is the
  active threshold; the default (0.6) applies until enough outcome evidence
  exists.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_followup_calibration"
down_revision: str | None = "0008_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "follow_up_suggestions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("application_records.id"),
            nullable=False,
        ),
        sa.Column("suggestion_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_follow_up_suggestions_user_id", "follow_up_suggestions", ["user_id"])
    op.create_index(
        "ix_follow_up_suggestions_application_id",
        "follow_up_suggestions",
        ["application_id"],
    )
    op.create_index(
        "ix_follow_up_suggestions_status", "follow_up_suggestions", ["status"]
    )

    op.create_table(
        "threshold_calibrations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("method", sa.String(64), nullable=False, server_default="quantile_p25"),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("details", sa.JSON, nullable=True),
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
    op.create_index("ix_threshold_calibrations_user_id", "threshold_calibrations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_threshold_calibrations_user_id", table_name="threshold_calibrations")
    op.drop_table("threshold_calibrations")
    op.drop_index("ix_follow_up_suggestions_status", table_name="follow_up_suggestions")
    op.drop_index("ix_follow_up_suggestions_application_id", table_name="follow_up_suggestions")
    op.drop_index("ix_follow_up_suggestions_user_id", table_name="follow_up_suggestions")
    op.drop_table("follow_up_suggestions")
