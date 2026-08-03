"""External-action approval boundary contracts.

These schemas define the durable representation of "the user approved this exact
planned action" for future external side effects (platform submit, HR message,
resume upload, profile fill, follow-up). They are the contract that
``assert_action_approved`` enforces before any external execution is allowed.

Design goals (see ``.trellis/tasks/08-01-approval-boundary-for-external-actions/``):

- An approval is bound to a *payload hash* and a *source snapshot*. If either
  changes, the approval is stale and execution is blocked.
- Approval is never a bare boolean. It records who approved, when, and the exact
  hash they approved.
- No raw secrets, cookies, tokens, full resume text, or browser/session data are
  stored in the payload preview or hash input.
- This task does **not** implement external execution; it only prepares the
  boundary that future platform tools must call.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class ExternalActionType(StrEnum):
    """The category of external side effect an action represents.

    Future platform tools must register against one of these types so the
    approval boundary can scope its guard. New types may be added here as the
    pilot platform grows.
    """

    platform_submit = "platform_submit"
    hr_message = "hr_message"
    resume_upload = "resume_upload"
    profile_fill = "profile_fill"
    follow_up_message = "follow_up_message"
    boss_immediate_communicate = "boss_immediate_communicate"


class ExternalActionStatus(StrEnum):
    """Lifecycle of a planned external action.

    The status flows roughly: ``draft`` → ``approval_required`` → ``approved``
    → (future execution). At any point before execution the approval may become
    ``stale`` (source/payload changed) or ``revoked`` (user cancelled). A
    ``blocked`` status means execution was attempted and refused.
    """

    draft = "draft"
    approval_required = "approval_required"
    approved = "approved"
    stale = "stale"
    revoked = "revoked"
    blocked = "blocked"


class ApprovalRecord(BaseSchema):
    """Durable record of a user's approval of one exact payload.

    ``approved_payload_hash`` must match the action's current ``payload_hash``
    for the approval to be valid. If the payload changes after approval, the
    action becomes ``stale`` and this record is retained for audit but no longer
    authorizes execution.
    """

    approved_by: str = Field(min_length=1, description="User ID that approved.")
    approved_at: datetime
    approved_payload_hash: str = Field(min_length=1, description="sha256:... hash approved.")


class ApplicationActionSourceSnapshot(BaseModel):
    """Source-identifiers bound to an action at preview time.

    These mirror the readiness source hash inputs. When the underlying job,
    resume version, profile, or generated artifacts change, the snapshot's
    ``source_hash`` changes and the action becomes stale.
    """

    job_id: str
    resume_version_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    source_hash: str = Field(min_length=1, description="Readiness source hash at preview time.")


class ApplicationActionPreview(BaseModel):
    """The exact planned external payload shown to the user for approval.

    This is the user-facing preview. It must contain only what the user needs to
    decide — **never** raw secrets, cookies, tokens, session data, or full
    resume text beyond what the outgoing payload actually sends. Outgoing
    message text is allowed because the user must see exactly what will be sent.
    """

    action_type: ExternalActionType
    target_platform: str | None = Field(
        default=None, description="e.g. 'boss', 'lagou'. None for manual/generic."
    )
    target_resource: str | None = Field(
        default=None,
        description="Platform resource identifier, e.g. job posting URL or HR conversation id.",
    )
    selected_artifact_ids: list[str] = Field(default_factory=list)
    outgoing_text: str | None = Field(
        default=None,
        description="Exact outgoing message/body text the user is approving, if any.",
    )
    resume_file_reference: str | None = Field(
        default=None,
        description="Identifier of the resume file/version to upload, if applicable.",
    )


class ApplicationActionCreate(BaseModel):
    """Request payload for creating a planned action (preview)."""

    action_type: ExternalActionType
    target_platform: str | None = None
    target_resource: str | None = None
    selected_artifact_ids: list[str] = Field(default_factory=list)
    outgoing_text: str | None = None
    resume_file_reference: str | None = None
    source_snapshot: ApplicationActionSourceSnapshot


class ExternalActionResultStatus(StrEnum):
    """Terminal status of an executed external side effect.

    Mirrors ``design.md §H1``: ``submitted`` is a confirmed success,
    ``duplicate`` means the platform already had this action (tied to the
    external idempotency key), ``unknown`` means the final state could not be
    classified and requires manual reconciliation, and ``failed`` means the
    platform rejected the action with a sanitized failure envelope.
    """

    submitted = "submitted"
    duplicate = "duplicate"
    unknown = "unknown"
    failed = "failed"


class ExternalActionResult(BaseModel):
    """Sanitized result metadata of an executed external side effect.

    ``result`` is safe metadata only — never raw cookies, tokens, page HTML,
    raw resume, or raw JD (design.md §H1, agent-safety-sandbox spec).
    """

    result_status: ExternalActionResultStatus
    started_at: datetime
    completed_at: datetime
    result: dict[str, Any] = Field(default_factory=dict)


class ApplicationActionOut(BaseSchema):
    """Outbound view of a planned external action with approval state."""

    id: str
    application_id: str
    user_id: str
    action_type: ExternalActionType
    status: ExternalActionStatus
    payload_preview: ApplicationActionPreview
    payload_hash: str = Field(min_length=1, description="sha256:... of the preview payload.")
    source_snapshot: ApplicationActionSourceSnapshot
    approval: ApprovalRecord | None = None
    stale_reason: str | None = None
    external_idempotency_key: str | None = None
    external_started_at: datetime | None = None
    external_completed_at: datetime | None = None
    external_result_status: ExternalActionResultStatus | None = None
    external_result: ExternalActionResult | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ApplicationActionListOut(BaseModel):
    items: list[ApplicationActionOut]


class ApprovalBlockedError(Exception):
    """Structured error raised when execution is attempted without valid
    approval.

    ``reason`` is a stable machine code; ``message`` is a safe user-facing
    string. The frontend uses ``next_action`` to decide what to surface.

    This is both a Pydantic model (for structured fields) and an ``Exception``
    so the execution guard can ``raise`` it and callers can catch it with
    ``except ApprovalBlockedError`` or ``pytest.raises``.
    """

    reason: str
    message: str
    current_payload_hash: str | None = None
    approved_payload_hash: str | None = None
    action_id: str
    next_action: str = "reapprove"

    class _NextAction(StrEnum):
        reapprove = "reapprove"
        recreate = "recreate"
        manual_review = "manual_review"

    def __init__(
        self,
        *,
        reason: str,
        message: str,
        action_id: str,
        current_payload_hash: str | None = None,
        approved_payload_hash: str | None = None,
        next_action: str = _NextAction.reapprove.value,
    ) -> None:
        self.reason = reason
        self.message = message
        self.action_id = action_id
        self.current_payload_hash = current_payload_hash
        self.approved_payload_hash = approved_payload_hash
        self.next_action = next_action
        super().__init__(reason, message, action_id)


__all__ = [
    "ApplicationActionCreate",
    "ApplicationActionListOut",
    "ApplicationActionOut",
    "ApplicationActionPreview",
    "ApplicationActionSourceSnapshot",
    "ApprovalBlockedError",
    "ApprovalRecord",
    "ExternalActionResult",
    "ExternalActionResultStatus",
    "ExternalActionStatus",
    "ExternalActionType",
]
