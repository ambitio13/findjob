"""Pydantic request/response schemas for the API layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    env: str
    version: str = "0.1.0"
    db: str | None = None
    redis: str | None = None


class PaginatedMeta(BaseModel):
    page: int = 1
    page_size: int = 20
    total: int = 0


# --- Jobs ---


class JobCreate(BaseModel):
    company: str
    title: str
    location: str | None = None
    salary_range: str | None = None
    direction: str | None = None
    jd_raw: str
    platform: str = "manual"
    jd_normalized: dict[str, Any] | None = None


class JobOut(BaseSchema):
    id: str
    platform: str
    company: str
    title: str
    location: str | None = None
    salary_range: str | None = None
    direction: str | None = None
    jd_raw: str
    jd_normalized: dict[str, Any] | None = None
    created_at: datetime | None = None


class JobListOut(BaseModel):
    meta: PaginatedMeta = Field(default_factory=PaginatedMeta)
    items: list[JobOut]


# --- Agent runs ---


class AgentRunOut(BaseSchema):
    id: str
    workflow_type: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime | None = None


class AgentStepOut(BaseSchema):
    """Outbound view of a persisted ``AgentStep`` row.

    Ordered by ``step_no`` so the frontend can render the run process as a
    timeline. ``result`` carries sanitized metadata only (counts, IDs, provider
    /model/prompt version, validation status, timing, error type/message) —
    never raw prompts, resume text, or full model payloads (R6). ``created_at``
    lets the frontend show step timestamps and ordering.
    """

    id: str
    run_id: str
    step_no: int
    name: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None


class AgentRunDetailOut(BaseSchema):
    """Run detail with ordered steps for the audit view (design.md read APIs).

    ``AgentRunOut`` stays the lightweight list shape; this extends it with
    ``steps`` so the frontend can render the process panel from a single read.
    """

    id: str
    workflow_type: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime | None = None
    steps: list[AgentStepOut] = Field(default_factory=list)


# --- Applications ---


class ApplicationCreate(BaseModel):
    """Create payload for a new application record.

    ``resume_version_id`` is optional at creation time: a record can start in
    ``planned`` without a resume, but cannot enter ``preparing`` until a usable
    (text-bearing) resume version is bound (PRD failure-handling requirement).
    """

    job_id: str
    resume_version_id: str | None = None


class ApplicationTimelineEventOut(BaseModel):
    """Outbound view of a persisted timeline event.

    The persisted ``ApplicationRecord.timeline`` is a JSON list whose entries
    follow the shape defined in ``design.md`` (id/type/at/actor/from_status/
    to_status/summary/metadata). This model validates that shape on read so the
    API contract is explicit even though storage is a free-form JSON column.
    """

    id: str
    type: str
    at: datetime
    actor: str = "user"
    from_status: str | None = None
    to_status: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApplicationOut(BaseSchema):
    """Outbound view of an ``ApplicationRecord``."""

    id: str
    user_id: str
    job_id: str
    resume_version_id: str | None = None
    status: str
    timeline: list[ApplicationTimelineEventOut] = Field(default_factory=list)
    latest_error: dict[str, Any] | None = None
    latest_agent_run_id: str | None = None
    readiness_snapshot: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ApplicationListOut(BaseModel):
    meta: PaginatedMeta = Field(default_factory=PaginatedMeta)
    items: list[ApplicationOut]


class ApplicationStatusUpdate(BaseModel):
    """Payload for ``PATCH /applications/{id}/status``.

    ``note`` is an optional safe user-facing note appended to the timeline.
    ``failure`` carries a sanitized failure envelope when the transition enters
    ``failed``; it is stored on ``latest_error`` and never contains raw text or
    secrets (enforced by ``build_failure_envelope`` upstream).
    """

    status: str
    note: str | None = None
    failure: dict[str, Any] | None = None
    agent_run_id: str | None = None


class ApplicationTimelineCreate(BaseModel):
    """Payload for ``POST /applications/{id}/timeline`` — append a manual note.

    Only user-note events may be appended through this endpoint; status-change
    events are owned by the status-update endpoint so the timeline never claims
    a transition that did not happen (design.md Transaction Rule).
    """

    summary: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ManualJdAnalysisDemoResponse(BaseModel):
    agent_run: AgentRunOut
    artifact_id: str
    artifact_type: str
    content: str


class ManualJdAnalysisDemoRequest(BaseModel):
    jd_text: str = Field(min_length=1)
