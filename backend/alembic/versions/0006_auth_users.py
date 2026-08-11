"""add auth_users table

Revision ID: 0006_auth_users
Revises: 0005_external_idempotency
Create Date: 2026-08-11

Adds the login-capable user table (Phase 0 security guardrail). ``id`` is a
stable internal key shared with the corresponding ``user_profiles`` row;
``username`` is unique. Only the PBKDF2 hash is stored — never plaintext
passwords, cookies, or platform credentials.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_auth_users"
down_revision: str | None = "0005_external_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
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
        sa.UniqueConstraint("username", name="uq_auth_users_username"),
    )
    op.create_index("ix_auth_users_username", "auth_users", ["username"])


def downgrade() -> None:
    op.drop_index("ix_auth_users_username", table_name="auth_users")
    op.drop_table("auth_users")
