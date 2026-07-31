"""Pydantic contract for JD paste parsing model output.

The model is asked to extract structured job fields from raw JD text pasted by
the user. This is the persistence gate (mirrors
:class:`~app.schemas.resume_facts.ResumeFactsModelOutput`): the executor parses
``ChatResponse.content`` as JSON and validates it into
:class:`JdPasteFactsModelOutput` before any parsed-draft response claims success
(per ``.trellis/spec/backend/ai-sdk-integration.md`` Prompt and Output Rules).

Every field is optional / default-empty so sparse JDs still produce a valid
object. ``uncertain_fields`` captures what needs user confirmation, satisfying
the PRD requirement that sparse parses surface a clear next action.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UncertainField(BaseModel):
    """A field the model could not confidently extract.

    ``reason`` explains what was missing or ambiguous so the user knows what to
    confirm or fill in manually.
    """

    field: str
    reason: str | None = None


class JdPasteFactsModelOutput(BaseModel):
    """Validated structured output produced by the JD paste parsing model.

    This is the persistence gate: the executor parses ``ChatResponse.content``
    as JSON and validates it into this model before the parse response claims
    success. All fields are optional to tolerate sparse JDs;
    ``uncertain_fields`` captures what needs user confirmation.
    """

    title: str | None = None
    company: str | None = None
    platform: str | None = None
    location: str | None = None
    salary_range: str | None = None
    direction: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    hard_requirements: list[str] = Field(default_factory=list)
    nice_to_have_requirements: list[str] = Field(default_factory=list)
    benefits_or_risk_clues: list[str] = Field(default_factory=list)
    uncertain_fields: list[UncertainField] = Field(default_factory=list)
