"""Pydantic schemas for resume upload, listing, and versions.

List/detail boundaries follow ``.trellis/spec/backend/api-contracts.md``: list
payloads stay small (no ``raw_text``), while the detail endpoint includes the
latest version's ``raw_text`` for display.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.api import PaginatedMeta
from app.schemas.common import BaseSchema


class ResumeVersionOut(BaseSchema):
    """Full version, including ``raw_text``. Used in the resume detail view."""

    id: str
    version_no: int
    parsed_facts: dict[str, Any] | None = None
    raw_text: str | None = None
    created_at: datetime | None = None


class ResumeVersionListItem(BaseModel):
    """Version summary without ``raw_text`` (keeps list payloads small)."""

    id: str
    version_no: int
    created_at: datetime | None = None
    parser_status: str | None = None
    parser_name: str | None = None


class ResumeOut(BaseSchema):
    """Resume summary used in list responses."""

    id: str
    filename: str
    mime_type: str | None = None
    created_at: datetime | None = None
    latest_version_no: int | None = None


class ResumeDetailOut(BaseSchema):
    """Resume detail, including the latest version's raw text."""

    id: str
    filename: str
    mime_type: str | None = None
    storage_uri: str | None = None
    created_at: datetime | None = None
    latest_version: ResumeVersionOut | None = None


class ResumeListOut(BaseModel):
    """Paginated list of resumes (no ``raw_text``)."""

    meta: PaginatedMeta = Field(default_factory=PaginatedMeta)
    items: list[ResumeOut]
