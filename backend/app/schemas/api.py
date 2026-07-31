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


class JobOut(BaseSchema):
    id: str
    platform: str
    company: str
    title: str
    location: str | None = None
    salary_range: str | None = None
    direction: str | None = None
    jd_raw: str
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


class ManualJdAnalysisDemoResponse(BaseModel):
    agent_run: AgentRunOut
    artifact_id: str
    artifact_type: str
    content: str


class ManualJdAnalysisDemoRequest(BaseModel):
    jd_text: str = Field(min_length=1)
