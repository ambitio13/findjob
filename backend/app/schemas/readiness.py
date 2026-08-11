"""Pydantic schemas for the readiness artifact generation workflow.

Two groups of schemas live here:

1. Structured output contracts (one per artifact type) — the shape the model
   must return and that Pydantic validates before any ``GeneratedArtifact`` row
   is written (per ``.trellis/spec/backend/ai-sdk-integration.md`` Prompt and
   Output Rules).
2. API request/response schemas used by the
   ``POST /applications/{id}/artifacts/{artifact_type}/generate`` endpoint.

No provider SDK is imported here; the structured outputs are provider-neutral.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class ReadinessArtifactType(StrEnum):
    """The readiness artifact types that can be generated for an application.

    Stored on ``GeneratedArtifact.artifact_type`` and used in the API path.
    """

    hr_opening_message = "hr_opening_message"
    resume_rewrite_snippet = "resume_rewrite_snippet"
    skill_gap_plan = "skill_gap_plan"
    interview_prep = "interview_prep"
    targeted_resume = "targeted_resume"


# ---------------------------------------------------------------------------
# Structured output contracts (design.md §Artifact Output Contracts)
# ---------------------------------------------------------------------------


class HrOpeningMessageOutput(BaseModel):
    """Validated structured output for ``hr_opening_message``.

    A concise, copy-paste-ready HR outreach message with source-backed evidence.
    """

    hook: str = Field(description="20字以内的亮点钩子。")
    message: str = Field(description="完整可复制的话术。")
    evidence: list[str] = Field(default_factory=list, description="来源支撑要点。")
    risk_note: str | None = Field(default=None, description="可选风险提示。")


class ResumeRewriteSnippetOutput(BaseModel):
    """Validated structured output for ``resume_rewrite_snippet``.

    Suggested resume edits keyed by section, plus a ``do_not_claim`` list of
    things the candidate should not assert (gaps or exaggerations).
    """

    project_snippets: list[str] = Field(default_factory=list)
    skill_snippets: list[str] = Field(default_factory=list)
    experience_snippets: list[str] = Field(default_factory=list)
    do_not_claim: list[str] = Field(default_factory=list)


class SkillGapPlanOutput(BaseModel):
    """Validated structured output for ``skill_gap_plan``.

    A prioritized study plan covering critical gaps, quick wins, a study
    schedule, and interview risks stemming from those gaps.
    """

    critical_gaps: list[str] = Field(default_factory=list)
    quick_wins: list[str] = Field(default_factory=list)
    study_plan: list[str] = Field(default_factory=list)
    interview_risk: list[str] = Field(default_factory=list)


class InterviewPrepOutput(BaseModel):
    """Validated structured output for ``interview_prep``.

    Likely interview questions, suggested answer points, portfolio talking
    points, and questions the candidate should ask the interviewer.
    """

    likely_questions: list[str] = Field(default_factory=list)
    answer_points: list[str] = Field(default_factory=list)
    portfolio_talking_points: list[str] = Field(default_factory=list)
    questions_to_ask_interviewer: list[str] = Field(default_factory=list)


class TargetedResumeBullet(BaseModel):
    """One JD-targeted resume bullet with mandatory fact provenance.

    ``source_fact_refs`` is the traceability contract: every bullet must cite
    at least one numbered resume fact ID (e.g. ``"P1"``, ``"W2"``) that was
    shown in the prompt. The executor rejects the whole output when any ref
    cannot be resolved against the extracted resume facts — this is the hard
    anti-hallucination gate for ``targeted_resume``.
    """

    section: str = Field(description="简历板块名（如 项目经历/工作经历/技能）。")
    bullet: str = Field(description="按 JD 关键词重排/改写后的条目正文。")
    matched_requirement: str = Field(
        description="该条目对应的 JD 要求或关键词。",
    )
    source_fact_refs: list[str] = Field(
        min_length=1,
        description="引用的简历事实编号（prompt 中编号列表的 ID），至少一个。",
    )


class TargetedResumeOutput(BaseModel):
    """Validated structured output for ``targeted_resume``.

    A one-page resume re-tailored to the JD. Every rewritten bullet is
    traceable to extracted resume facts via ``source_fact_refs``; the model
    must never invent experience. ``do_not_claim`` lists claims the resume
    does NOT support, and ``one_page_markdown`` is the copy-ready page.
    """

    headline: str = Field(description="一行求职定位语（针对该岗位）。")
    targeted_bullets: list[TargetedResumeBullet] = Field(
        min_length=1,
        description="按 JD 重排/改写的简历条目，每条必须溯源。",
    )
    matched_requirements: list[str] = Field(
        default_factory=list,
        description="简历已满足的 JD 要求。",
    )
    do_not_claim: list[str] = Field(
        default_factory=list,
        description="简历无支撑、不可声称的能力或经历。",
    )
    one_page_markdown: str = Field(description="一页式简历 Markdown 全文。")


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------


class RunReadinessRunSummary(BaseModel):
    """Lightweight run summary surfaced in the generate submit response.

    Carries just enough for the frontend to display the run status and poll
    ``GET /agent-runs/{run_id}/detail`` until a terminal status is reached,
    mirroring :class:`~app.schemas.jd_analysis.RunJdAnalysisRunSummary`.
    """

    id: str
    status: str
    error: str | None = None

    model_config = {"from_attributes": True}


class RunReadinessSubmitResponse(BaseModel):
    """Immediate response for the generate endpoint (enqueue-and-poll).

    The endpoint creates a ``queued`` ``AgentRun`` and enqueues the readiness
    generation job to the worker queue, then returns immediately with this
    response. The frontend polls ``GET /agent-runs/{run_id}/detail`` until the
    run reaches a terminal status (``succeeded`` or ``failed``), then hydrates
    the artifact from the persisted ``GeneratedArtifact`` row.
    """

    run: RunReadinessRunSummary
    application_id: str
    artifact_type: ReadinessArtifactType


class ReadinessArtifactOut(BaseSchema):
    """Outbound view of a persisted ``GeneratedArtifact`` row.

    ``content`` is the validated structured-output JSON string; the frontend
    can re-parse it into the corresponding output model for rendering.
    """

    id: str
    user_id: str | None = None
    job_id: str | None = None
    resume_version_id: str | None = None
    agent_run_id: str | None = None
    artifact_type: str
    source_ids: dict[str, Any] | None = None
    prompt_version: str | None = None
    model_name: str | None = None
    content: str
    created_at: datetime | None = None


class ReadinessArtifactListOut(BaseModel):
    """List response for readiness artifacts bound to an application."""

    items: list[ReadinessArtifactOut] = Field(default_factory=list)
