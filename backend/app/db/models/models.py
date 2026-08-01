"""SQLAlchemy ORM models for the MVP skeleton.

Entities cover user profile, resume versions, job postings, JD analysis,
generated artifacts, application records, and agent runtime state (runs,
steps, tool calls). Models are migration-ready; see ``alembic/`` for the
initial migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    career_direction: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preferred_locations: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strengths: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    constraints: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    resumes: Mapped[list[Resume]] = relationship(back_populates="user")


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class Resume(Base, TimestampMixin):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user: Mapped[UserProfile] = relationship(back_populates="resumes")
    versions: Mapped[list[ResumeVersion]] = relationship(back_populates="resume")


class ResumeVersion(Base, TimestampMixin):
    __tablename__ = "resume_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    resume_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("resumes.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parsed_facts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    resume: Mapped[Resume] = relationship(back_populates="versions")


# ---------------------------------------------------------------------------
# Job posting + JD analysis
# ---------------------------------------------------------------------------


class JobPosting(Base, TimestampMixin):
    __tablename__ = "job_postings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    company: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    salary_range: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(64), nullable=True)

    jd_raw: Mapped[str] = mapped_column(Text, nullable=False)
    jd_normalized: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    analyses: Mapped[list[JobAnalysis]] = relationship(back_populates="job")
    applications: Mapped[list[ApplicationRecord]] = relationship(back_populates="job")


class JobAnalysis(Base, TimestampMixin):
    __tablename__ = "job_analyses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("job_postings.id"), nullable=False, index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    growth_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    stability_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[JobPosting] = relationship(back_populates="analyses")


# ---------------------------------------------------------------------------
# Generated artifact + application
# ---------------------------------------------------------------------------


class GeneratedArtifact(Base, TimestampMixin):
    __tablename__ = "generated_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("job_postings.id"), nullable=True, index=True
    )
    resume_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # One of: jd_analysis, hr_opening_message, resume_rewrite_snippet,
    # skill_gap_plan, interview_prep
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_ids: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class ApplicationRecord(Base, TimestampMixin):
    __tablename__ = "application_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # Direct user scoping + index for list views (design.md "Data Model Notes").
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("job_postings.id"), nullable=False, index=True
    )
    resume_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", index=True)
    # Append-only JSON list of ApplicationTimelineEvent dicts.
    timeline: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # Latest failure envelope (sanitized) when the record entered ``failed``.
    latest_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Latest agent run id linked to this application (provenance for retry/audit).
    latest_agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Snapshot of readiness-source metadata (source_hash + version stamps) used
    # to detect stale artifacts without re-reading every source row.
    readiness_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    job: Mapped[JobPosting] = relationship(back_populates="applications")


# ---------------------------------------------------------------------------
# Agent runtime state
# ---------------------------------------------------------------------------


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Optional job scoping so failed runs (which create no JobAnalysis row) can
    # still be listed per-job. Demo runs leave this NULL.
    job_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("job_postings.id"), nullable=True, index=True
    )

    steps: Mapped[list[AgentStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentStep(Base, TimestampMixin):
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class ToolCall(Base, TimestampMixin):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    input: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
