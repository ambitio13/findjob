"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-31

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # user_profiles
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("career_direction", sa.String(64), nullable=True),
        sa.Column("base_location", sa.String(128), nullable=True),
        sa.Column("preferred_locations", sa.JSON, nullable=True),
        sa.Column("salary_min", sa.Integer, nullable=True),
        sa.Column("salary_max", sa.Integer, nullable=True),
        sa.Column("strengths", sa.JSON, nullable=True),
        sa.Column("constraints", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # resumes
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("user_profiles.id"), nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # resume_versions
    op.create_table(
        "resume_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("resume_id", sa.String(64), sa.ForeignKey("resumes.id"), nullable=False, index=True),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("parsed_facts", sa.JSON, nullable=True),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # job_postings
    op.create_table(
        "job_postings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("user_profiles.id"), nullable=True, index=True),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("salary_range", sa.String(128), nullable=True),
        sa.Column("direction", sa.String(64), nullable=True),
        sa.Column("jd_raw", sa.Text, nullable=False),
        sa.Column("jd_normalized", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # job_analyses
    op.create_table(
        "job_analyses",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
        sa.Column("agent_run_id", sa.String(64), nullable=True, index=True),
        sa.Column("match_score", sa.Float, nullable=True),
        sa.Column("risk_score", sa.Float, nullable=True),
        sa.Column("salary_analysis", sa.JSON, nullable=True),
        sa.Column("growth_analysis", sa.JSON, nullable=True),
        sa.Column("stability_analysis", sa.JSON, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # generated_artifacts
    op.create_table(
        "generated_artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True, index=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("job_postings.id"), nullable=True, index=True),
        sa.Column("resume_version_id", sa.String(64), nullable=True, index=True),
        sa.Column("agent_run_id", sa.String(64), nullable=True, index=True),
        sa.Column("artifact_type", sa.String(64), nullable=False, index=True),
        sa.Column("source_ids", sa.JSON, nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # application_records
    op.create_table(
        "application_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
        sa.Column("resume_version_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("timeline", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True, index=True),
        sa.Column("workflow_type", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # agent_steps
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("agent_runs.id"), nullable=False, index=True),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # tool_calls
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=True, index=True),
        sa.Column("step_id", sa.String(64), nullable=True, index=True),
        sa.Column("tool_name", sa.String(128), nullable=False, index=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("input", sa.JSON, nullable=True),
        sa.Column("output", sa.JSON, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.BigInteger, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("application_records")
    op.drop_table("generated_artifacts")
    op.drop_table("job_analyses")
    op.drop_table("job_postings")
    op.drop_table("resume_versions")
    op.drop_table("resumes")
    op.drop_table("user_profiles")
