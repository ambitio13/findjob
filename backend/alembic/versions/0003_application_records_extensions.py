"""extend application_records with user scoping, failure, and snapshot

Revision ID: 0003_app_records_ext
Revises: 0002_agent_run_job_id
Create Date: 2026-08-01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_app_records_ext"
down_revision: str | None = "0002_agent_run_job_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extend application_records with explicit user scoping, latest failure
    # envelope, latest agent-run provenance, and a readiness-source snapshot.
    # ``user_id`` is NOT NULL going forward; existing rows (if any) are
    # back-filled from the linked job's owner before the NOT NULL constraint
    # is applied so no orphaned records are left behind.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {column["name"] for column in inspector.get_columns("application_records")}

    if "user_id" not in columns:
        # Add as nullable first so we can back-fill from job_postings.user_id.
        op.add_column(
            "application_records",
            sa.Column("user_id", sa.String(64), nullable=True),
        )
        # Back-fill from the owning job's user_id.
        op.execute(
            """
            UPDATE application_records AS ar
            SET user_id = j.user_id
            FROM job_postings AS j
            WHERE ar.job_id = j.id
            """
        )
        # Now enforce NOT NULL + foreign key + index.
        op.alter_column(
            "application_records",
            "user_id",
            existing_type=sa.String(64),
            nullable=False,
        )

    foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("application_records")
        if fk["constrained_columns"] == ["user_id"]
    }
    if "fk_application_records_user_id_user_profiles" not in foreign_keys:
        op.create_foreign_key(
            "fk_application_records_user_id_user_profiles",
            "application_records",
            "user_profiles",
            ["user_id"],
            ["id"],
        )

    indexes = {index["name"] for index in inspector.get_indexes("application_records")}
    if "ix_application_records_user_id" not in indexes:
        op.create_index(
            "ix_application_records_user_id",
            "application_records",
            ["user_id"],
        )

    if "latest_error" not in columns:
        op.add_column(
            "application_records",
            sa.Column("latest_error", sa.JSON, nullable=True),
        )

    if "latest_agent_run_id" not in columns:
        op.add_column(
            "application_records",
            sa.Column("latest_agent_run_id", sa.String(64), nullable=True),
        )

    if "readiness_snapshot" not in columns:
        op.add_column(
            "application_records",
            sa.Column("readiness_snapshot", sa.JSON, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {column["name"] for column in inspector.get_columns("application_records")}

    if "readiness_snapshot" in columns:
        op.drop_column("application_records", "readiness_snapshot")

    if "latest_agent_run_id" in columns:
        op.drop_column("application_records", "latest_agent_run_id")

    if "latest_error" in columns:
        op.drop_column("application_records", "latest_error")

    indexes = {index["name"] for index in inspector.get_indexes("application_records")}
    if "ix_application_records_user_id" in indexes:
        op.drop_index("ix_application_records_user_id", table_name="application_records")

    foreign_keys = {
        fk["name"]
        for fk in inspector.get_foreign_keys("application_records")
        if fk["constrained_columns"] == ["user_id"]
    }
    if "fk_application_records_user_id_user_profiles" in foreign_keys:
        op.drop_constraint(
            "fk_application_records_user_id_user_profiles",
            "application_records",
            type_="foreignkey",
        )

    if "user_id" in columns:
        op.drop_column("application_records", "user_id")
