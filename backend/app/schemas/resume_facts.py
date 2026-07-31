"""Pydantic contract for resume fact extraction model output.

The model is asked to extract structured resume facts from raw resume text.
This is the persistence gate (mirrors
:class:`~app.schemas.jd_analysis.JdAnalysisModelOutput`): the executor parses
``ChatResponse.content`` as JSON and validates it into
:class:`ResumeFactsModelOutput` before any ``parsed_facts.facts`` write claims
success (per ``.trellis/spec/backend/ai-sdk-integration.md`` Prompt and Output
Rules).

Every field is optional / default-empty so sparse resumes still produce a valid
object. ``uncertain_fields`` captures what needs user confirmation, satisfying
the PRD requirement that sparse resumes surface a clear next action.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Contact(BaseModel):
    """Contact info extracted from the resume."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None


class EducationItem(BaseModel):
    """One education entry."""

    school: str | None = None
    degree: str | None = None
    major: str | None = None
    period: str | None = None


class WorkExperienceItem(BaseModel):
    """One work experience entry."""

    company: str | None = None
    title: str | None = None
    period: str | None = None
    summary: str | None = None


class ProjectItem(BaseModel):
    """One project entry."""

    name: str | None = None
    role: str | None = None
    summary: str | None = None


class UncertainField(BaseModel):
    """A field the model could not confidently extract.

    ``reason`` explains what was missing or ambiguous so the user knows what to
    confirm or fill in.
    """

    field: str
    reason: str | None = None


class ResumeFactsModelOutput(BaseModel):
    """Validated structured output produced by the resume fact extraction.

    This is the persistence gate: the executor parses ``ChatResponse.content``
    as JSON and validates it into this model before any ``parsed_facts.facts``
    write claims success. All fields are optional to tolerate sparse resumes;
    ``uncertain_fields`` captures what needs user confirmation.
    """

    contact: Contact | None = None
    education: list[EducationItem] = Field(default_factory=list)
    work_experience: list[WorkExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    years_of_experience: int | None = None
    target_direction: str | None = None
    locations: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    uncertain_fields: list[UncertainField] = Field(default_factory=list)
