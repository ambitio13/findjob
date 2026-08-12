"""Pydantic schemas for the BOSS match-decision workflow.

Two groups of schemas live here:

1. Structured output contract (``MatchDecisionModelOutput``) — the shape the
   model must return and that Pydantic validates before any
   ``GeneratedArtifact`` row is written. This is the persistence gate.
2. API request/response schemas (``MatchJobRequest`` / ``MatchDecisionOut``)
   used by the ``POST /boss/recommended-jobs/{job_id}/match`` endpoint.

The output contract follows ``design.md`` §Match Decision: ``decision``,
``score``, ``reasons``, ``risks``, ``missing_requirements``,
``opening_message``. ``opening_message`` is ``None`` when the decision is not
``communicate``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class MatchDecision(StrEnum):
    """Allowed match-decision values.

    - ``communicate`` — eligible to prepare a platform action (subject to the
      post-model safety gate).
    - ``skip`` — record the reason and stop; no browser side effect.
    - ``needs_review`` — show the user a review UI and stop before any browser
      side effect.
    """

    communicate = "communicate"
    skip = "skip"
    needs_review = "needs_review"


class MatchDecisionModelOutput(BaseModel):
    """Validated structured output produced by the model gateway.

    This is the persistence gate: the executor parses ``ChatResponse.content``
    as JSON and validates it into this model before any ``GeneratedArtifact``
    row is written. ``score`` is a float in ``[0.0, 1.0]``.

    ``opening_message`` is ``None`` when the decision is not ``communicate``.
    Even when the model returns a message with ``communicate``, the
    :func:`~app.agents.opening_message_guard.apply_match_safety_gate` may
    nullify it and downgrade the decision to ``needs_review``.
    """

    decision: MatchDecision
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    opening_message: str | None = None


class MatchJobRequest(BaseModel):
    """Body of ``POST /boss/recommended-jobs/{job_id}/match``."""

    resume_version_id: str = Field(min_length=1)


class MatchDecisionOut(BaseSchema):
    """Response payload for the match-decision endpoint.

    ``message`` carries a human-readable explanation when the safety gate
    downgraded the decision (e.g. ``"low confidence, downgraded to
    needs_review"``).

    ``draft_opening_message`` is the **pre-gate** model opening message,
    returned only when the safety gate downgraded the decision and the model
    produced a message. It lets the human-review UI prefill the draft the user
    edits. The persisted artifact stores only the post-gate output; this field
    is response-only and never persisted. ``None`` when no downgrade happened
    or the model produced no message.
    """

    decision: MatchDecision
    score: float
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    opening_message: str | None = None
    job_id: str
    agent_run_id: str | None = None
    artifact_id: str | None = None
    message: str | None = None
    draft_opening_message: str | None = None


__all__ = [
    "MatchDecision",
    "MatchDecisionModelOutput",
    "MatchDecisionOut",
    "MatchJobRequest",
]
