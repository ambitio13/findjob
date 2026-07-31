"""Pydantic schemas for the current-user profile and job-search preferences.

These schemas own transport-level validation (salary range, email format).
Field names mirror the ``UserProfile`` ORM model columns so the service layer
can apply updates without a mapping layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.schemas.common import BaseSchema


class UserProfileRead(BaseSchema):
    """Outbound view of the current user's profile and preferences."""

    id: str
    display_name: str
    email: str | None = None
    career_direction: str | None = None
    base_location: str | None = None
    preferred_locations: list[str] | None = None  # JSON list in DB
    salary_min: int | None = None
    salary_max: int | None = None
    strengths: list[str] | None = None  # JSON list in DB
    constraints: dict[str, Any] | None = None  # flexible JSON
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserProfileUpdate(BaseModel):
    """Inbound partial update for the current user's profile.

    All fields are optional. A field omitted from the request body is left
    untouched; a field sent as ``null`` clears the underlying nullable column.
    Use PATCH semantics.
    """

    display_name: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None
    career_direction: str | None = None
    base_location: str | None = None
    preferred_locations: list[str] | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    strengths: list[str] | None = None
    constraints: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_salary_range(self) -> UserProfileUpdate:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must be <= salary_max")
        return self
