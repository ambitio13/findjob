"""Pydantic schemas for application outcomes and funnel metrics.

Outcomes close the feedback loop (Phase 1): they record what *actually
happened* after a submission (HR replied / rejected / interview / offer) so
match scores and opening-message variants can be calibrated against real
results instead of guesswork.

Privacy contract: ``evidence`` is a short human summary only. Chat content,
cookies, tokens, and platform session data must never be stored there.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema

#: Maximum evidence length — a short summary, never a chat transcript.
EVIDENCE_MAX_LEN = 500


class OutcomeType(StrEnum):
    replied = "replied"
    rejected = "rejected"
    interview = "interview"
    offer = "offer"


class OutcomeSource(StrEnum):
    manual = "manual"
    userscript_observed = "userscript_observed"


class OutcomeCreate(BaseModel):
    outcome_type: OutcomeType
    #: Defaults to now when omitted.
    occurred_at: datetime | None = None
    source: OutcomeSource = OutcomeSource.manual
    evidence: str | None = Field(default=None, max_length=EVIDENCE_MAX_LEN)


class OutcomeOut(BaseSchema):
    id: str
    application_id: str
    outcome_type: OutcomeType
    source: OutcomeSource
    occurred_at: datetime
    evidence: str | None = None
    created_at: datetime | None = None


class OutcomeListOut(BaseModel):
    items: list[OutcomeOut]


class MatchScoreBucketOut(BaseModel):
    """Funnel metrics bucketed by the job's match score (calibration view)."""

    bucket: str = Field(description="e.g. '<0.6', '0.6-0.8', '>=0.8', 'unknown'")
    applications: int
    replied: int
    interviews: int


class OpeningPromptBucketOut(BaseModel):
    """Funnel metrics bucketed by the opening message's prompt version.

    Answers "which opening prompt actually works": applications whose latest
    ``hr_opening_message`` artifact was generated with a given prompt version.
    ``prompt_version`` is ``"unknown"`` when no opening was generated or the
    artifact predates prompt versioning.
    """

    prompt_version: str
    applications: int
    replied: int
    interviews: int


class FunnelMetricsOut(BaseModel):
    """User-scoped submission funnel with outcome rates.

    Rates are computed over applications that reached ``submitted`` (or
    beyond): an application still in ``preparing`` says nothing about the
    quality of an opening message.
    """

    applications_total: int
    submitted: int
    with_reply: int
    interviews: int
    offers: int
    rejected: int
    #: replied+ among submitted, or None when no application was submitted.
    reply_rate: float | None = None
    interview_rate: float | None = None
    by_match_score: list[MatchScoreBucketOut] = Field(default_factory=list)
    #: Bucketed by the latest opening-message prompt version per job.
    by_opening_prompt: list[OpeningPromptBucketOut] = Field(default_factory=list)
