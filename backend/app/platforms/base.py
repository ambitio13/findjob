"""Typed platform adapter boundary (design.md §Adapter Contract).

This module defines the contracts every platform adapter must implement. Product
services depend on these types — never on concrete adapters — so selectors, DOM
traversal, and browser retry logic stay isolated behind a typed boundary.

Two-phase contract:

- :meth:`PlatformAdapter.prepare_submission` — dry-run fill. Navigates to the
  platform form using the user's existing browser session, classifies the page
  state, fills safe fields, and returns a sanitized
  :class:`FilledSubmissionSnapshot` ready for approval. **Never** performs the
  final submit action.
- :meth:`PlatformAdapter.submit_prepared` — final submit. Called only after the
  approval boundary (:func:`app.services.approval_boundary.assert_action_approved`)
  and the external idempotency guard (design.md §H1) have both passed.
- :meth:`PlatformAdapter.execute_communication` — immediate communicate. Clicks
  "立即沟通", fills the opening message, sends it, and reads the post-send state.
  Called only after the approval boundary + idempotency guards pass for a
  ``boss_immediate_communicate`` action. Click budget: at most one
  ``click_immediate_communicate`` and one ``send_opening_message`` per call.

The adapter must never solve CAPTCHA, bypass rate limits, or keep clicking when
selectors drift. Those are hard stops surfaced as
:class:`PrepareResult` / :class:`SubmitResult` variants (design.md §Failure
Handling). No cookies, tokens, credentials, raw JD, raw resume, or page HTML may
appear in any persisted result.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class PrepareContext(BaseModel):
    """Inputs for a dry-run platform submission (fill-only, no final submit).

    ``session_reference`` is an opaque, user-provided handle to an *existing*
    browser session (e.g. a Playwright storage-state path or a session cookie
    jar kept in memory). The adapter must never persist it, log it, or store it
    on any durable row. ``target_resource`` is the platform resource the form
    lives on (job posting URL or HR conversation id).
    """

    application_id: str = Field(min_length=1)
    target_platform: str = Field(min_length=1)
    target_resource: str = Field(min_length=1)
    selected_artifact_ids: list[str] = Field(default_factory=list)
    outgoing_text: str | None = None
    resume_file_reference: str | None = None
    source_hash: str = Field(min_length=1, description="Readiness source hash at prepare time.")
    session_reference: str | None = Field(
        default=None,
        description="Opaque handle to a user-provided browser session. Never persisted.",
    )


class SubmitContext(BaseModel):
    """Inputs for a final platform submit.

    Carries the exact filled snapshot the user approved plus the same opaque
    session reference. The adapter performs the final submit action only.
    """

    application_id: str = Field(min_length=1)
    target_platform: str = Field(min_length=1)
    target_resource: str = Field(min_length=1)
    filled_snapshot: FilledSubmissionSnapshot
    session_reference: str | None = None


# ---------------------------------------------------------------------------
# Sanitized snapshot
# ---------------------------------------------------------------------------


class FilledField(BaseModel):
    """One filled form field shown to the user for approval."""

    name: str = Field(min_length=1)
    label: str | None = None
    value: str | None = Field(
        default=None,
        description="Safe visible text the user is approving. Never a secret/cookie/token.",
    )
    source_artifact_id: str | None = None


class FilledAttachment(BaseModel):
    """One attachment staged for upload, shown to the user for approval."""

    kind: str = Field(min_length=1, description="e.g. 'resume'.")
    display_name: str | None = None
    reference: str | None = Field(
        default=None,
        description="Opaque file/version reference. Never raw file bytes or a token.",
    )


class FilledPageState(BaseModel):
    """Sanitized page state captured at fill time.

    Only safe descriptors are kept: a URL hash, the page title, and whether the
    final-submit control was visible. Raw HTML, cookies, and headers must never
    appear here.
    """

    url_hash: str | None = None
    title: str | None = None
    final_submit_selector_seen: bool = False


class FilledSubmissionSnapshot(BaseModel):
    """Sanitized snapshot of exactly what would be submitted (design.md).

    This is the dry-run output the user reviews and approves. It must contain
    only what the user needs to decide — **never** raw secrets, cookies, tokens,
    session data, raw JD, or raw resume text beyond the outgoing field values
    the user is approving.
    """

    target_platform: str = Field(min_length=1)
    target_resource: str = Field(min_length=1)
    application_id: str = Field(min_length=1)
    selected_artifact_ids: list[str] = Field(default_factory=list)
    resume_file_reference: str | None = None
    fields: list[FilledField] = Field(default_factory=list)
    attachments: list[FilledAttachment] = Field(default_factory=list)
    page_state: FilledPageState = Field(default_factory=Field)
    captured_at: datetime


# ---------------------------------------------------------------------------
# Prepare results
# ---------------------------------------------------------------------------


class PrepareOutcome(StrEnum):
    """Classified outcome of a dry-run prepare attempt."""

    filled_preview = "filled_preview"
    login_required = "login_required"
    captcha_required = "captcha_required"
    selector_drift = "selector_drift"
    rate_limited = "rate_limited"
    duplicate_detected = "duplicate_detected"
    upload_failed = "upload_failed"
    unknown = "unknown"


class PrepareResult(BaseModel):
    """Result of :meth:`PlatformAdapter.prepare_submission`.

    Only the ``filled_preview`` variant carries a :class:`FilledSubmissionSnapshot`.
    Every other variant is a hard stop: the adapter detected a platform state it
    must not work around, and the workflow must persist a sanitized failure
    envelope instead of retrying blindly.
    """

    outcome: PrepareOutcome
    snapshot: FilledSubmissionSnapshot | None = None
    failure_code: str | None = Field(
        default=None,
        description=(
            "Stable envelope code for non-filled outcomes "
            "(e.g. 'platform_login_required')."
        ),
    )
    message: str | None = None
    diagnostic_reference: str | None = Field(
        default=None,
        description="Opaque local diagnostic reference/path. Never cookies, tokens, or page HTML.",
    )


# ---------------------------------------------------------------------------
# Submit results
# ---------------------------------------------------------------------------


class SubmitOutcome(StrEnum):
    """Classified outcome of a final submit attempt."""

    submitted = "submitted"
    duplicate_detected = "duplicate_detected"
    unknown = "unknown"
    platform_failure = "platform_failure"


class SubmitResult(BaseModel):
    """Result of :meth:`PlatformAdapter.submit_prepared`.

    ``submitted`` is a confirmed success. ``duplicate_detected`` means the
    platform already had this action (tied to the external idempotency key).
    ``unknown`` means the final state could not be classified and requires
    manual reconciliation. ``platform_failure`` carries a sanitized failure
    envelope code for retry/manual-review routing.
    """

    outcome: SubmitOutcome
    platform_reference: str | None = Field(
        default=None,
        description="Safe platform-side reference for a confirmed submit, if any.",
    )
    failure_code: str | None = None
    message: str | None = None
    diagnostic_reference: str | None = None
    occurred_at: datetime


# ---------------------------------------------------------------------------
# Communication execute results
# ---------------------------------------------------------------------------


class CommunicationOutcome(StrEnum):
    """Classified outcome of an immediate-communicate execute attempt.

    ``succeeded`` is a confirmed message send. ``duplicate`` means a
    conversation already existed for this job/contact (not a failure).
    ``failed`` carries a sanitized failure envelope code (button missing,
    input missing, etc.). ``unknown`` means the post-send state could not be
    classified and requires manual reconciliation — no automatic retry.
    """

    succeeded = "succeeded"
    duplicate = "duplicate"
    failed = "failed"
    unknown = "unknown"


class CommunicationExecuteContext(BaseModel):
    """Inputs for an immediate-communicate execute.

    Carries the opening message text (validated for length/tone/PII at prepare
    time) plus the same opaque session reference used by the submit flow.
    ``target_resource`` is the page URL hash binding the action to the exact
    job page the user approved.
    """

    application_id: str = Field(min_length=1)
    target_platform: str = Field(min_length=1)
    target_resource: str = Field(
        min_length=1,
        description="Page URL hash binding the action to the approved job page.",
    )
    opening_message: str = Field(
        min_length=1,
        description="Validated opening message text to send via the chat dialog.",
    )
    source_hash: str = Field(min_length=1, description="Readiness source hash at execute time.")
    session_reference: str | None = None


class CommunicationExecuteResult(BaseModel):
    """Result of :meth:`PlatformAdapter.execute_communication`.

    ``succeeded`` is a confirmed send. ``duplicate`` means a conversation
    already existed. ``failed`` carries a failure code for manual-review
    routing. ``unknown`` is a hard stop requiring manual reconciliation —
    no automatic retry.
    """

    outcome: CommunicationOutcome
    platform_reference: str | None = Field(
        default=None,
        description="Safe platform-side reference for a confirmed communication, if any.",
    )
    failure_code: str | None = None
    message: str | None = None
    diagnostic_reference: str | None = None
    occurred_at: datetime


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PlatformAdapter(Protocol):
    """Typed boundary between product services and a concrete platform.

    Implementations live under ``app/platforms/<platform>/adapter.py``. The real
    BOSS adapter is enabled only behind an explicit environment flag; tests use
    a fake adapter for deterministic behavior.
    """

    platform: str

    async def prepare_submission(self, ctx: PrepareContext) -> PrepareResult:
        """Dry-run fill. Returns a filled preview or a hard-stop classification.

        Must **never** perform the final submit action.
        """
        ...

    async def submit_prepared(self, ctx: SubmitContext) -> SubmitResult:
        """Final submit. Called only after approval + idempotency guards pass."""
        ...

    async def execute_communication(
        self, ctx: CommunicationExecuteContext
    ) -> CommunicationExecuteResult:
        """Click "立即沟通" and send the opening message.

        Called only after the approval boundary + idempotency guards pass for a
        ``boss_immediate_communicate`` action. The adapter clicks the
        immediate-communicate button, fills the opening message into the chat
        dialog, sends it, and reads the post-send page state to classify the
        result. Click budget: at most one ``click_immediate_communicate`` and
        one ``send_opening_message`` per call.
        """
        ...


# ---------------------------------------------------------------------------
# Failure-code mapping (design.md §Failure Handling)
# ---------------------------------------------------------------------------

#: Maps each non-filled prepare outcome to the sanitized failure envelope code
#: and next-action the workflow must persist. Keeping the mapping here means
#: product services never hard-code platform failure strings.
PREPARE_FAILURE_CODES: dict[PrepareOutcome, tuple[str, str]] = {
    PrepareOutcome.login_required: ("platform_login_required", "manual_review"),
    PrepareOutcome.captcha_required: ("platform_captcha_required", "manual_review"),
    PrepareOutcome.selector_drift: ("platform_selector_drift", "manual_review"),
    PrepareOutcome.rate_limited: ("platform_rate_limited", "manual_review"),
    PrepareOutcome.duplicate_detected: ("platform_duplicate_detected", "manual_review"),
    PrepareOutcome.upload_failed: ("platform_upload_failed", "retry"),
    PrepareOutcome.unknown: ("platform_unknown_result", "manual_review"),
}

#: Maps each non-submitted submit outcome to the sanitized failure envelope
#: code and next-action.
SUBMIT_FAILURE_CODES: dict[SubmitOutcome, tuple[str, str]] = {
    SubmitOutcome.duplicate_detected: ("platform_duplicate_detected", "manual_review"),
    SubmitOutcome.unknown: ("platform_unknown_result", "manual_review"),
    SubmitOutcome.platform_failure: ("platform_failure", "manual_review"),
}

#: Maps each non-succeeded communication outcome to the sanitized failure
#: envelope code and next-action. ``duplicate`` is NOT a failure — it maps to
#: its own code so the workflow can route it as "conversation already exists"
#: rather than retrying.
COMMUNICATION_FAILURE_CODES: dict[CommunicationOutcome, tuple[str, str]] = {
    CommunicationOutcome.duplicate: ("communication_duplicate_detected", "manual_review"),
    CommunicationOutcome.failed: ("communication_failure", "manual_review"),
    CommunicationOutcome.unknown: ("communication_unknown_result", "manual_review"),
}


def prepare_failure_code(outcome: PrepareOutcome) -> str:
    """Return the sanitized envelope code for a non-filled prepare outcome."""
    if outcome is PrepareOutcome.filled_preview:
        raise ValueError("filled_preview is not a failure outcome")
    return PREPARE_FAILURE_CODES[outcome][0]


def prepare_failure_next_action(outcome: PrepareOutcome) -> str:
    """Return the next-action string for a non-filled prepare outcome."""
    if outcome is PrepareOutcome.filled_preview:
        raise ValueError("filled_preview is not a failure outcome")
    return PREPARE_FAILURE_CODES[outcome][1]


def submit_failure_code(outcome: SubmitOutcome) -> str:
    """Return the sanitized envelope code for a non-submitted submit outcome."""
    if outcome is SubmitOutcome.submitted:
        raise ValueError("submitted is not a failure outcome")
    return SUBMIT_FAILURE_CODES[outcome][0]


def submit_failure_next_action(outcome: SubmitOutcome) -> str:
    """Return the next-action string for a non-submitted submit outcome."""
    if outcome is SubmitOutcome.submitted:
        raise ValueError("submitted is not a failure outcome")
    return SUBMIT_FAILURE_CODES[outcome][1]


def communication_failure_code(outcome: CommunicationOutcome) -> str:
    """Return the sanitized envelope code for a non-succeeded communicate outcome."""
    if outcome is CommunicationOutcome.succeeded:
        raise ValueError("succeeded is not a failure outcome")
    return COMMUNICATION_FAILURE_CODES[outcome][0]


def communication_failure_next_action(outcome: CommunicationOutcome) -> str:
    """Return the next-action string for a non-succeeded communicate outcome."""
    if outcome is CommunicationOutcome.succeeded:
        raise ValueError("succeeded is not a failure outcome")
    return COMMUNICATION_FAILURE_CODES[outcome][1]


__all__ = [
    "COMMUNICATION_FAILURE_CODES",
    "CommunicationExecuteContext",
    "CommunicationExecuteResult",
    "CommunicationOutcome",
    "FilledAttachment",
    "FilledField",
    "FilledPageState",
    "FilledSubmissionSnapshot",
    "PlatformAdapter",
    "PREPARE_FAILURE_CODES",
    "PrepareContext",
    "PrepareOutcome",
    "PrepareResult",
    "SUBMIT_FAILURE_CODES",
    "SubmitContext",
    "SubmitOutcome",
    "SubmitResult",
    "communication_failure_code",
    "communication_failure_next_action",
    "prepare_failure_code",
    "prepare_failure_next_action",
    "submit_failure_code",
    "submit_failure_next_action",
]


# Re-exported here so ``from app.platforms.base import FilledSubmissionSnapshot``
# works for callers that only need the snapshot type. SubmitContext references
# FilledSubmissionSnapshot via a forward ref resolved below.
SubmitContext.model_rebuild()
