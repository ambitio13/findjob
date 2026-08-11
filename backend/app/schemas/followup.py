"""Pydantic schemas for the Phase 5 follow-up loop.

Covers advisory follow-up suggestions (daily-scan output) and the
match-threshold calibration view. Suggestions never trigger external
effects: acting on one re-enters the existing generation + approval flow.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class SuggestionType(StrEnum):
    change_opening_message = "change_opening_message"
    skill_gap_plan = "skill_gap_plan"
    low_reply_rate_direction = "low_reply_rate_direction"


class SuggestionStatus(StrEnum):
    pending = "pending"
    actioned = "actioned"
    dismissed = "dismissed"


class FollowUpSuggestionOut(BaseSchema):
    id: str
    application_id: str
    suggestion_type: SuggestionType
    title: str
    detail: str | None = None
    status: SuggestionStatus
    resolved_at: datetime | None = None
    created_at: datetime | None = None


class FollowUpSuggestionListOut(BaseModel):
    items: list[FollowUpSuggestionOut]


class FollowUpScanOut(BaseModel):
    """Result of one follow-up scan for the current user."""

    created: int = Field(description="Number of newly created suggestions.")
    suggestions: list[FollowUpSuggestionOut] = Field(
        default_factory=list, description="Suggestions created by this scan."
    )
    #: Active communicate-gate threshold after the scan's calibration step.
    active_threshold: float
    #: True when the scan produced a new calibration row this run.
    calibrated_now: bool = False


class MatchThresholdOut(BaseModel):
    """The active communicate-gate threshold and where it came from."""

    threshold: float
    #: ``calibrated`` when derived from outcome data, ``default`` otherwise.
    source: str
    method: str | None = None
    sample_count: int | None = None
    details: dict[str, Any] | None = None
    calibrated_at: datetime | None = None
