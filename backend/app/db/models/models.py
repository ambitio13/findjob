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
from app.db.types import EncryptedText


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthUser(Base, TimestampMixin):
    """A login-capable user (username + PBKDF2 password hash).

    ``id`` doubles as the ``UserProfile.id`` the request is attributed to, so
    the auth identity and the profile row share one stable key. The password
    hash never leaves this table; logs and API responses must never carry it.
    """

    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)


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

    # Raw JD text is user-supplied long content: encrypted at rest via
    # EncryptedText (plaintext is never persisted; see app/core/content_crypto).
    jd_raw: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    jd_normalized: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Phase 4 multi-platform entry: the listing URL the user pasted the JD
    # from (any platform). Pure provenance metadata, never fetched by us.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # Phase 3 job-risk lens: sanitized red-flag summary
    # ([{"flag_type","title","severity"}]). NULL = no flags (so the job list
    # can filter with a dialect-neutral ``IS NOT NULL``). Evidence quotes stay
    # on the GeneratedArtifact, never here.
    red_flags: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

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
    # skill_gap_plan, interview_prep, targeted_resume
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
    actions: Mapped[list[ApplicationAction]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    outcomes: Mapped[list[ApplicationOutcome]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# External-action approval boundary
# ---------------------------------------------------------------------------


class ApplicationAction(Base, TimestampMixin):
    """A planned external action awaiting user approval.

    One row per planned side effect (platform submit, HR message, resume upload,
    profile fill, follow-up). The action binds the exact preview payload (via
    ``payload_hash``) and the readiness source snapshot (via ``source_hash`` in
    ``source_snapshot``) at preview time. Approval is recorded in
    ``approval`` (who/when/hash); if payload or source later drifts the action
    becomes ``stale`` and execution is blocked by
    ``app.services.approval_boundary.assert_action_approved``.

    No secrets, cookies, tokens, or browser/session data are stored in
    ``payload_preview`` or ``source_snapshot`` — only identifiers, outgoing
    message text, and hashes.
    """

    __tablename__ = "application_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("application_records.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="approval_required", index=True
    )
    payload_preview: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    approval: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # External idempotency key for retryable side effects. Shape:
    # ``{application_id}:{action_type}:{payload_hash}``. Stored before any
    # platform call; ``external_idempotency_key`` uniqueness on terminal
    # actions prevents duplicate platform submits (design.md §H1).
    external_idempotency_key: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    external_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ``submitted | duplicate | unknown | failed``. Sanitized result metadata
    # only — never raw cookies/tokens/page HTML (design.md §H1).
    external_result_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    application: Mapped[ApplicationRecord] = relationship(back_populates="actions")


# ---------------------------------------------------------------------------
# Application outcomes (feedback loop, Phase 1)
# ---------------------------------------------------------------------------


class ApplicationOutcome(Base, TimestampMixin):
    """An append-only outcome event observed for an application.

    Outcomes close the feedback loop: HR replied / rejected / interview
    scheduled / offer. They are recorded manually by the user today; a later
    userscript read-only scan may add ``userscript_observed`` events. Multiple
    rows per application are allowed (outcomes evolve over time); queries use
    the latest relevant row per type.

    ``evidence`` stores a short sanitized summary only — never chat content,
    cookies, or platform session data.
    """

    __tablename__ = "application_outcomes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("application_records.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    # ``replied | rejected | interview | offer``
    outcome_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # ``manual | userscript_observed``
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    application: Mapped[ApplicationRecord] = relationship(back_populates="outcomes")


# ---------------------------------------------------------------------------
# Follow-up suggestions + threshold calibration (Phase 5 feedback loop)
# ---------------------------------------------------------------------------


class FollowUpSuggestion(Base, TimestampMixin):
    """An advisory follow-up suggestion produced by the daily scan.

    Suggestions are evidence-driven nudges (e.g. "submitted 3 days ago with
    no reply → try a different opening message"). They are advisory only:
    acting on a suggestion always re-enters the existing generation +
    approval boundary, so a suggestion row never triggers external effects.
    """

    __tablename__ = "follow_up_suggestions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("application_records.id"), nullable=False, index=True
    )
    # ``change_opening_message | skill_gap_plan | low_reply_rate_direction``
    suggestion_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ``pending | actioned | dismissed``
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThresholdCalibration(Base, TimestampMixin):
    """An append-only match-threshold calibration derived from outcome data.

    The newest row per user is the active ``COMMUNICATE_MIN_SCORE``; the
    module default applies until enough evidence exists. ``details`` keeps the
    statistics (sample counts, raw quantile) so a calibration can be audited.
    """

    __tablename__ = "threshold_calibrations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_profiles.id"), nullable=False, index=True
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False, default="quantile_p25")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


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
