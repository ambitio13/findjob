"""Pydantic schemas for the current-user profile and job-search preferences.

These schemas own transport-level validation (salary range, email format).
Direct columns on ``UserProfile`` are mirrored by name. The flexible
``constraints`` JSON column is exposed through named, typed sub-fields (see
``ProfileConstraints``) so the UI never has to send or display raw JSON.
Legacy unknown keys inside ``constraints`` are preserved and surfaced as
read-only ``legacy_constraints``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.schemas.common import BaseSchema

#: The named keys that the v1 profile form maps into the ``constraints`` JSON
#: column. Any key inside ``constraints`` that is NOT in this set is treated as
#: legacy data and surfaced read-only via ``legacy_constraints``.
NAMED_CONSTRAINT_KEYS: frozenset[str] = frozenset(
    {
        "deal_breakers",
        "preferred_company_types",
        "preferred_industries",
        "work_mode_preference",
        "commute_preference",
        "career_goals",
        "resume_tailoring_notes",
        "availability_notes",
    }
)


class ProfileConstraints(BaseModel):
    """Named, typed view of the ``constraints`` JSON column.

    All fields are optional text-entry fields. ``list[str]`` fields accept
    comma-separated or tag-style input from the UI; the service normalizes
    them before storage.
    """

    deal_breakers: str | None = None
    preferred_company_types: str | None = None
    preferred_industries: str | None = None
    work_mode_preference: str | None = None
    commute_preference: str | None = None
    career_goals: str | None = None
    resume_tailoring_notes: str | None = None
    availability_notes: str | None = None


class UserProfileRead(BaseSchema):
    """Outbound view of the current user's profile and preferences.

    ``constraints`` is split into named typed fields (``named_constraints``)
    plus a read-only ``legacy_constraints`` dict for unknown keys that pre-date
    the structured form.
    """

    id: str
    display_name: str
    email: str | None = None
    career_direction: str | None = None
    base_location: str | None = None
    preferred_locations: list[str] | None = None  # JSON list in DB
    salary_min: int | None = None
    salary_max: int | None = None
    strengths: list[str] | None = None  # JSON list in DB
    named_constraints: ProfileConstraints = Field(default_factory=ProfileConstraints)
    legacy_constraints: dict[str, Any] | None = None  # unknown keys, read-only
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserProfileUpdate(BaseModel):
    """Inbound partial update for the current user's profile.

    All fields are optional. A field omitted from the request body is left
    untouched; a field sent as ``null`` clears the underlying value.

    Named constraint fields are mapped into the ``constraints`` JSON column by
    the service. ``legacy_constraints`` is not accepted on update — the form
    only writes known named keys.
    """

    display_name: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None
    career_direction: str | None = None
    base_location: str | None = None
    preferred_locations: list[str] | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    strengths: list[str] | None = None
    named_constraints: ProfileConstraints | None = None

    @model_validator(mode="after")
    def _check_salary_range(self) -> UserProfileUpdate:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must be <= salary_max")
        return self
