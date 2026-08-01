"""Application readiness state machine and failure envelope contracts.

These schemas define the durable domain contracts every application-readiness
workflow depends on:

- ``ApplicationStatus`` — constrained status set and allowed transition table
  (mirrored in ``app.services.application_state``).
- ``ApplicationFailureEnvelope`` — the safe, user-facing shape of a failed
  readiness operation. It deliberately carries only IDs, counts, stable error
  codes, and short messages — never raw resume/JD text, prompts, or secrets.
- ``ApplicationSourceSnapshot`` — a stable hash over *identifiers and versions*
  (job, resume version, profile, prompt versions) used to detect stale
  artifacts. The hash input is metadata only, not raw text.

These contracts are backend-owned. The frontend may mirror display labels
later, but durable state lives here.

See `.trellis/tasks/08-01-application-state-machine-and-failure-envelope/` for
the full PRD and design rationale.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class ApplicationStatus(StrEnum):
    """Constrained application lifecycle states.

    Allowed transitions are defined in
    ``app.services.application_state.TRANSITIONS``.
    """

    planned = "planned"
    preparing = "preparing"
    materials_ready = "materials_ready"
    approval_required = "approval_required"
    approved = "approved"
    submitted = "submitted"
    failed = "failed"
    paused = "paused"
    rejected = "rejected"
    interviewing = "interviewing"


class ApplicationFailureCategory(StrEnum):
    """High-level category of a readiness-operation failure.

    Used by the UI to group error display and by the backend to route retry
    logic. ``unknown`` is a fallback that must trigger manual review.
    """

    queue = "queue"
    model = "model"
    validation = "validation"
    data = "data"
    user_action = "user_action"
    platform = "platform"
    unknown = "unknown"


class ApplicationFailureNextAction(StrEnum):
    """The concrete next step a user (or the system) should take.

    This is the actionable counterpart to ``retryable``: even a retryable
    failure may need the user to pick a different resume first.
    """

    retry = "retry"
    edit_source = "edit_source"
    choose_resume = "choose_resume"
    reapprove = "reapprove"
    manual_review = "manual_review"


class ApplicationSourceSnapshot(BaseSchema):
    """Stable metadata snapshot used to detect stale artifacts.

    Only identifiers, timestamps, and version stamps are hashed — never raw
    JD text, raw resume text, or prompt content. When any of these fields
    change, the ``source_hash`` changes and previously generated artifacts
    are considered stale.
    """

    job_id: str
    job_updated_at: datetime | None = None
    resume_version_id: str | None = None
    resume_version_no: int | None = None
    profile_updated_at: datetime | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    source_hash: str = Field(min_length=1)


class ApplicationFailureEnvelope(BaseSchema):
    """Safe, user-facing representation of a failed readiness operation.

    .. warning::

       Never store raw resume text, raw JD text, model prompt content,
       uploaded file bytes, cookies, tokens, or platform credentials in this
       envelope. Only safe metadata (IDs, counts, source hash, stable error
       code, short user-facing message) belongs here.
    """

    category: ApplicationFailureCategory
    code: str = Field(min_length=1, description="Stable machine-readable error code.")
    message: str = Field(min_length=1, description="Safe user-facing message.")
    retryable: bool
    next_action: ApplicationFailureNextAction
    agent_run_id: str | None = None
    source_ids: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class ApplicationTimelineEvent(BaseModel):
    """A single append-only timeline entry for an application record.

    Timeline events explain every state change and operation outcome. They
    are persisted as a JSON list on ``ApplicationRecord.timeline``.
    """

    event: str = Field(min_length=1, description="Stable event name, e.g. 'status_change'.")
    from_status: ApplicationStatus | None = None
    to_status: ApplicationStatus | None = None
    operation: str | None = Field(
        default=None, description="Operation type if this event records an operation."
    )
    agent_run_id: str | None = None
    message: str | None = None
    occurred_at: datetime


class ActiveOperationKey(BaseModel):
    """Identity key for detecting duplicate active operations.

    Two operations with the same ``operation_type``, ``application_id``,
    ``resume_version_id``, and ``source_hash`` that are both ``queued`` or
    ``running`` are considered duplicates. See
    ``app.services.application_state`` for the enforcement contract.

    The model is hashable so callers can use it in ``set`` / ``dict`` for
    in-memory dedup checks.
    """

    operation_type: str
    application_id: str
    resume_version_id: str | None = None
    source_hash: str

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(
            (
                self.operation_type,
                self.application_id,
                self.resume_version_id,
                self.source_hash,
            )
        )
