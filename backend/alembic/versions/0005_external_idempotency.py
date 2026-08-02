"""add external idempotency columns to application_actions

Revision ID: 0005_external_idempotency
Revises: 0004_application_actions
Create Date: 2026-08-02

Adds durable external-action idempotency storage to ``application_actions``
so the platform pilot can prevent duplicate platform submits when the user
retries or the agent re-runs (design.md §H1, PRD acceptance criterion H1).

- ``external_idempotency_key`` — ``{application_id}:{action_type}:{payload_hash}``;
  indexed so the submit guard can look up a terminal result cheaply.
- ``external_started_at`` / ``external_completed_at`` — bounded audit window.
- ``external_result_status`` — ``submitted | duplicate | unknown | failed``.
- ``external_result`` — sanitized JSON metadata only (never cookies/tokens/
  page HTML/raw resume/raw JD).

The key is nullable so existing previewed/approved actions created before this
migration remain valid; the platform submission service populates it before
any platform call.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_external_idempotency"
down_revision: str | None = "0004_application_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "application_actions",
        sa.Column("external_idempotency_key", sa.String(160), nullable=True),
    )
    op.add_column(
        "application_actions",
        sa.Column("external_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_actions",
        sa.Column("external_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_actions",
        sa.Column("external_result_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "application_actions",
        sa.Column("external_result", sa.JSON, nullable=True),
    )
    op.create_index(
        "ix_application_actions_external_idempotency_key",
        "application_actions",
        ["external_idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_actions_external_idempotency_key",
        table_name="application_actions",
    )
    op.drop_column("application_actions", "external_result")
    op.drop_column("application_actions", "external_result_status")
    op.drop_column("application_actions", "external_completed_at")
    op.drop_column("application_actions", "external_started_at")
    op.drop_column("application_actions", "external_idempotency_key")
