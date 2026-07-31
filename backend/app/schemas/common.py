"""Shared type definitions for model and agent runtime contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    """Generate a prefixed sortable ID (e.g. ``run_...``, ``step_...``)."""
    return f"{prefix}_{uuid.uuid4().hex}"


class BaseSchema(BaseModel):
    model_config = {"from_attributes": True}


class TimestampMixin(BaseModel):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JobSearchDirection(StrEnum):
    engineering = "engineering"
    product = "product"
    design = "design"
    data = "data"
    other = "other"


class JSONable(BaseModel):
    """Marker base for payloads stored as JSON/JSONB."""

    data: dict[str, Any] = Field(default_factory=dict)
