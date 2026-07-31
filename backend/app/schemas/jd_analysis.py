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


class JdAnalysisRiskPoint(BaseModel):
    """A single risk point surfaced by the analysis."""

    title: str
    detail: str
    severity: Severity


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


class RunJdAnalysisResponse(BaseModel):
    """Response shape for ``POST /jobs/{job_id}/analyses`` (design.md §5.1).

    ``structured`` is the validated model output, echoed back so the frontend
    can render it without re-parsing ``artifact.content``.
    """

    agent_run: dict[str, Any]
    analysis: JobAnalysisOut
    artifact: GeneratedArtifactOut
    structured: JdAnalysisModelOutput


class JobAnalysisListOut(BaseModel):
    """Paginated list of analyses for a job (design.md §5.2)."""

    meta: dict[str, Any] = Field(default_factory=dict)
    items: list[JobAnalysisOut]
