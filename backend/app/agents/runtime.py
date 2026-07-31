"""Lightweight agent runtime types.

These types describe the durable concepts of an agent workflow. They are
designed to be persisted (see ``app.db.models.models``) but the runtime can
also operate on in-memory instances for the smoke workflow.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class StepStatus(StrEnum):
    planned = "planned"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class ArtifactType(StrEnum):
    jd_analysis = "jd_analysis"
    hr_opening_message = "hr_opening_message"
    resume_rewrite_snippet = "resume_rewrite_snippet"
    skill_gap_plan = "skill_gap_plan"
    interview_prep = "interview_prep"


class AgentRunData(BaseModel):
    """In-memory representation of an agent run (mirrors the ORM model)."""

    id: str
    user_id: str | None = None
    workflow_type: str
    status: RunStatus = RunStatus.queued
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    steps: list[AgentStepData] = Field(default_factory=list)


class AgentStepData(BaseModel):
    id: str
    run_id: str
    step_no: int
    name: str
    status: StepStatus = StepStatus.planned
    result: dict[str, Any] | None = None
    error: str | None = None


class ToolCallData(BaseModel):
    id: str
    run_id: str | None = None
    step_id: str | None = None
    tool_name: str
    schema_version: str = "1"
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    status: str = "pending"
    latency_ms: int | None = None
    retry_count: int = 0


class ArtifactData(BaseModel):
    id: str
    user_id: str | None = None
    job_id: str | None = None
    resume_version_id: str | None = None
    agent_run_id: str | None = None
    artifact_type: ArtifactType
    source_ids: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_name: str | None = None
    content: str
