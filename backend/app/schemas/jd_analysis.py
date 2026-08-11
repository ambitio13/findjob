"""Pydantic schemas for the resume-aware JD analysis workflow.

Two groups of schemas live here:

1. Structured output contract (``JdAnalysisModelOutput`` and its parts) — the
   shape the model must return and that Pydantic validates before any
   persistence claims success (per ``.trellis/spec/backend/ai-sdk-integration.md``
   Prompt and Output Rules).
2. API request/response schemas (``RunJdAnalysisRequest`` etc.) used by the
   ``/jobs/{job_id}/analyses`` endpoints defined in Phase 4.

No provider SDK is imported here; the structured output is provider-neutral.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema

# ---------------------------------------------------------------------------
# Structured output contract (design.md §6)
# ---------------------------------------------------------------------------

Severity = Literal["low", "medium", "high"]
EvidenceSource = Literal["jd", "resume", "profile"]
Recommendation = Literal[
    "strong_match",
    "possible_match",
    "weak_match",
    "not_enough_info",
]

#: Known scam/quality red-flag categories the job-risk lens detects
#: (Phase 3). ``other`` is the escape hatch for patterns outside this list.
RedFlagType = Literal[
    "training_loan",      # 培训贷话术
    "training_fee",       # 岗前培训费/押金
    "outsourcing_onsite", # 外包驻场
    "inflated_salary",    # 薪资虚高（区间过宽/不切实际）
    "long_term_listing",  # 常年挂单
    "other",
]

#: Cap for evidence quotes so an artifact never becomes a full JD mirror.
_EVIDENCE_QUOTE_MAX_LEN = 300


class JdAnalysisRiskPoint(BaseModel):
    """A single risk point surfaced by the analysis."""

    title: str
    detail: str
    severity: Severity


class JdRedFlag(BaseModel):
    """One job-quality red flag with a JD original-text evidence quote.

    ``evidence_quote`` must be a short verbatim excerpt from the JD (the
    detail "岗位透视" panel renders it next to the conclusion); it is capped
    so artifacts never mirror large JD chunks.
    """

    flag_type: RedFlagType
    title: str
    detail: str
    severity: Severity
    evidence_quote: str | None = Field(
        default=None, max_length=_EVIDENCE_QUOTE_MAX_LEN
    )


class JdSalaryStructure(BaseModel):
    """Structured salary parsing (Phase 3): range, period, and composition.

    ``min_value``/``max_value`` are normalized to k/month where derivable;
    ``caveats`` lists why the number may not be trustworthy (over-wide range,
    "综合薪资" wording, etc.).
    """

    range_text: str | None = Field(default=None, description="JD 原文薪资表述。")
    min_value: float | None = Field(default=None, ge=0, description="归一化下限(k/月)。")
    max_value: float | None = Field(default=None, ge=0, description="归一化上限(k/月)。")
    period: Literal["monthly", "yearly", "hourly", "daily", "unknown"] = "unknown"
    composition: list[str] = Field(
        default_factory=list, description="构成项（底薪/绩效/提成/补贴…）。"
    )
    caveats: list[str] = Field(
        default_factory=list, description="薪资不可信/虚高的理由。"
    )


class JdStabilitySignal(BaseModel):
    """One company-stability signal with optional JD evidence quote."""

    polarity: Literal["positive", "negative", "unknown"]
    signal: str
    evidence_quote: str | None = Field(
        default=None, max_length=_EVIDENCE_QUOTE_MAX_LEN
    )


class JdAnalysisEvidence(BaseModel):
    """One piece of evidence tying a claim back to a source document.

    ``quote`` must come verbatim from the cited ``source``. When the model is
    unsure of an exact quote it must omit ``quote`` and explain the uncertainty
    in a ``risk_points`` entry instead.
    """

    claim: str
    source: EvidenceSource
    quote: str | None = None


class JdAnalysisModelOutput(BaseModel):
    """Validated structured output produced by the model gateway.

    This is the persistence gate: the executor parses ``ChatResponse.content``
    as JSON and validates it into this model before any ``JobAnalysis`` or
    ``GeneratedArtifact`` row is written. ``match_score`` / ``risk_score`` are
    optional because they only apply when resume raw text is present.
    """

    role_summary: str
    responsibilities: list[str]
    hard_requirements: list[str]
    nice_to_have_requirements: list[str]
    resume_match_evidence: list[JdAnalysisEvidence]
    risk_points: list[JdAnalysisRiskPoint]
    salary_note: str
    growth_note: str
    stability_note: str
    match_score: int | None = Field(default=None, ge=0, le=100)
    risk_score: int | None = Field(default=None, ge=0, le=100)
    skill_gaps: list[str]
    interview_preparation: list[str]
    recommendation: Recommendation
    # Phase 3 job-risk lens. All optional so artifacts produced before the
    # schema extension still validate (fields simply stay empty).
    salary_structure: JdSalaryStructure | None = None
    red_flags: list[JdRedFlag] = Field(default_factory=list)
    stability_signals: list[JdStabilitySignal] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API request / response schemas (design.md §5)
# ---------------------------------------------------------------------------


class RunJdAnalysisRequest(BaseModel):
    """Body of ``POST /api/v1/jobs/{job_id}/analyses``."""

    resume_version_id: str = Field(min_length=1)


class JobAnalysisOut(BaseSchema):
    """Outbound view of a persisted ``JobAnalysis`` row."""

    id: str
    job_id: str
    agent_run_id: str | None = None
    match_score: float | None = None
    risk_score: float | None = None
    summary: str | None = None
    salary_analysis: dict[str, Any] | None = None
    growth_analysis: dict[str, Any] | None = None
    stability_analysis: dict[str, Any] | None = None
    created_at: datetime | None = None


class GeneratedArtifactOut(BaseSchema):
    """Outbound view of a ``GeneratedArtifact`` row.

    ``content`` is the validated structured-output JSON string; large generated
    documents are returned by ID/metadata in list views per
    ``.trellis/spec/backend/api-contracts.md``.
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


class JobAnalysisDetailOut(BaseSchema):
    """A persisted analysis plus the artifact needed to reconstruct the result.

    The list endpoint returns one of these per analysis so the frontend can
    hydrate the full result view (structured output + artifact metadata) from
    persisted rows alone, without relying on the original POST response (R1/R2).
    """

    analysis: JobAnalysisOut
    artifact: GeneratedArtifactOut | None = None
    structured: JdAnalysisModelOutput | None = None


class RunJdAnalysisResponse(BaseModel):
    """Response shape for ``POST /jobs/{job_id}/analyses`` (design.md §5.1).

    ``structured`` is the validated model output, echoed back so the frontend
    can render it without re-parsing ``artifact.content``.

    .. deprecated:: queue-migration

       Retained for older callers and the synchronous test path. The enqueue-
       and-poll endpoint now returns :class:`RunJdAnalysisSubmitResponse`.
    """

    agent_run: dict[str, Any]
    analysis: JobAnalysisOut
    artifact: GeneratedArtifactOut
    structured: JdAnalysisModelOutput


class RunJdAnalysisRunSummary(BaseModel):
    """Lightweight run summary surfaced in the analysis submit response.

    Carries just enough for the frontend to display the run status and poll
    ``GET /agent-runs/{run_id}/detail`` until a terminal status is reached,
    mirroring :class:`~app.schemas.jd_parse.JdParseRunSummary`.
    """

    id: str
    status: str
    error: str | None = None

    model_config = {"from_attributes": True}


class RunJdAnalysisSubmitResponse(BaseModel):
    """Immediate response for ``POST /jobs/{job_id}/analyses`` (enqueue-and-poll).

    The endpoint creates a ``queued`` ``AgentRun`` and enqueues the analysis
    job to the worker queue, then returns immediately with this response. The
    frontend polls ``GET /agent-runs/{run_id}/detail`` until the run reaches a
    terminal status (``succeeded`` or ``failed``), then hydrates the analysis
    from the persisted ``JobAnalysis`` / ``GeneratedArtifact`` rows.

    ``resume_version_id`` is echoed back so the form retains the user's
    selection while polling.
    """

    run: RunJdAnalysisRunSummary
    resume_version_id: str


class JobAnalysisListOut(BaseModel):
    """Paginated list of analyses for a job (design.md §5.2).

    Each item is a ``JobAnalysisDetailOut`` so the frontend can render persisted
    results directly. The artifact's ``content`` is re-parsed into ``structured``
    here; rows whose content cannot be parsed degrade to ``structured=None``
    instead of breaking the whole list (design.md Compatibility).
    """

    meta: dict[str, Any] = Field(default_factory=dict)
    items: list[JobAnalysisDetailOut]
