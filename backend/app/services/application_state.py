"""Application state machine, source snapshot, and failure envelope service.

This module owns the durable domain rules every application-readiness workflow
depends on:

- **Transition table** — which ``ApplicationStatus`` values may follow which.
- **Source snapshot** — a stable hash over job/resume/profile/prompt-version
  *metadata* (never raw text) so stale artifacts can be detected.
- **Failure envelope** — a safe builder that strips raw text, prompts, and
  secrets before persisting a failure.
- **Duplicate active-operation contract** — how callers detect and reject
  duplicate in-flight work for the same job/resume/operation.

Contracts only. No database writes happen here; later tasks wire these rules
into the application records center and readiness-artifact workflows.

See ``.trellis/tasks/08-01-application-state-machine-and-failure-envelope/``
for the PRD and design.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.schemas.application import (
    ActiveOperationKey,
    ApplicationFailureCategory,
    ApplicationFailureEnvelope,
    ApplicationFailureNextAction,
    ApplicationSourceSnapshot,
    ApplicationStatus,
)

_log = get_logger("app.services.application_state")


class InvalidTransitionError(ValueError):
    """Raised when a status transition is not allowed by the state machine.

    Carries ``from_status`` and ``to_status`` so callers can surface a clear
    validation error (HTTP 422 at the API boundary) without leaking internal
    state.
    """

    def __init__(self, from_status: ApplicationStatus, to_status: ApplicationStatus) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"invalid status transition: {from_status.value} -> {to_status.value}"
        )


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

#: Allowed forward/lateral transitions. ``rejected`` may return to ``planned``
#: so a user can re-attempt after a known rejection. ``paused`` is a safe
#: holding state reachable from most active statuses and can resume to either
#: ``planned`` or ``preparing``.
TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.planned: frozenset(
        {ApplicationStatus.preparing, ApplicationStatus.paused}
    ),
    ApplicationStatus.preparing: frozenset(
        {ApplicationStatus.materials_ready, ApplicationStatus.failed, ApplicationStatus.paused}
    ),
    ApplicationStatus.failed: frozenset(
        {ApplicationStatus.preparing, ApplicationStatus.paused}
    ),
    ApplicationStatus.materials_ready: frozenset(
        {ApplicationStatus.approval_required, ApplicationStatus.preparing, ApplicationStatus.paused}
    ),
    ApplicationStatus.approval_required: frozenset(
        {ApplicationStatus.approved, ApplicationStatus.preparing, ApplicationStatus.paused}
    ),
    ApplicationStatus.approved: frozenset(
        {
            ApplicationStatus.submitted,
            ApplicationStatus.approval_required,
            ApplicationStatus.failed,
            ApplicationStatus.paused,
        }
    ),
    ApplicationStatus.submitted: frozenset(
        {ApplicationStatus.interviewing, ApplicationStatus.rejected, ApplicationStatus.paused}
    ),
    ApplicationStatus.paused: frozenset(
        {ApplicationStatus.planned, ApplicationStatus.preparing}
    ),
    ApplicationStatus.interviewing: frozenset(
        {ApplicationStatus.rejected, ApplicationStatus.paused}
    ),
    ApplicationStatus.rejected: frozenset({ApplicationStatus.planned}),
}


def can_transition(from_status: ApplicationStatus, to_status: ApplicationStatus) -> bool:
    """Return ``True`` if ``from_status -> to_status`` is an allowed transition."""
    return to_status in TRANSITIONS.get(from_status, frozenset())


def assert_transition(
    from_status: ApplicationStatus, to_status: ApplicationStatus
) -> None:
    """Raise ``InvalidTransitionError`` if the transition is not allowed."""
    if not can_transition(from_status, to_status):
        _log.warning(
            "application.invalid_transition",
            from_status=from_status.value,
            to_status=to_status.value,
        )
        raise InvalidTransitionError(from_status, to_status)


# ---------------------------------------------------------------------------
# Source snapshot
# ---------------------------------------------------------------------------

#: Fields included in the source hash. Order is fixed for hash stability.
_HASH_FIELDS: tuple[str, ...] = (
    "job_id",
    "job_updated_at",
    "resume_version_id",
    "resume_version_no",
    "profile_updated_at",
    "prompt_versions",
)

#: Sentinel serialized into the hash so a missing timestamp is never confused
#: with a present one (and ``None`` never appears as the bare string "None").
_NONE_SENTINEL = "\x00none\x00"


def _stable_json(value: Any) -> str:
    """Serialize ``value`` into a deterministic JSON string.

    ``sort_keys=True`` guarantees that two snapshots with the same fields but
    different dict insertion order produce the same hash. Datetime objects are
    rendered as ISO 8601 strings via the default serializer.
    """
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), default=str)


def _normalize(value: Any) -> Any:
    """Normalize values for deterministic JSON serialization.

    ``None`` is mapped to a sentinel so it never collides with the string
    ``"None"``. Mappings are recursed into. Other JSON-native types pass
    through unchanged.
    """
    if value is None:
        return _NONE_SENTINEL
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def compute_source_hash(snapshot: ApplicationSourceSnapshot) -> str:
    """Return a stable ``sha256:...`` hash over snapshot metadata only.

    The hash covers job/resume/profile identifiers and prompt versions —
    **never** raw JD text, raw resume text, or prompt content. Any change to
    these metadata fields changes the hash, which is how stale artifacts are
    detected.
    """
    payload = {field: getattr(snapshot, field) for field in _HASH_FIELDS}
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_source_snapshot(
    *,
    job_id: str,
    job_updated_at: datetime | None = None,
    resume_version_id: str | None = None,
    resume_version_no: int | None = None,
    profile_updated_at: datetime | None = None,
    prompt_versions: Mapping[str, str] | None = None,
) -> ApplicationSourceSnapshot:
    """Build an ``ApplicationSourceSnapshot`` with a computed ``source_hash``.

    Callers should pass the *current* metadata of the job, resume version,
    profile, and prompt versions they intend to operate on. When any of these
    change later, a fresh snapshot will produce a different ``source_hash``,
    marking previously generated artifacts as stale.
    """
    snapshot = ApplicationSourceSnapshot(
        job_id=job_id,
        job_updated_at=job_updated_at,
        resume_version_id=resume_version_id,
        resume_version_no=resume_version_no,
        profile_updated_at=profile_updated_at,
        prompt_versions=dict(prompt_versions or {}),
        source_hash="pending",  # placeholder; overwritten below
    )
    return snapshot.model_copy(update={"source_hash": compute_source_hash(snapshot)})


# ---------------------------------------------------------------------------
# Failure envelope
# ---------------------------------------------------------------------------

#: Source-id key fragments that should never be copied into a failure envelope.
#: This blocks both long raw text and short-but-sensitive values such as
#: ``jd_raw="Python"`` or ``access_token="..."``.
_SENSITIVE_SOURCE_ID_KEY_PARTS = frozenset(
    {
        "raw",
        "text",
        "prompt",
        "secret",
        "token",
        "cookie",
        "credential",
        "password",
        "file_bytes",
    }
)


def build_failure_envelope(
    *,
    category: ApplicationFailureCategory,
    code: str,
    message: str,
    retryable: bool,
    next_action: ApplicationFailureNextAction,
    agent_run_id: str | None = None,
    source_ids: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> ApplicationFailureEnvelope:
    """Build a safe ``ApplicationFailureEnvelope``.

    .. warning::

       ``message`` must be a safe, user-facing string. Callers must not pass
       raw resume text, raw JD text, model prompt content, file bytes, tokens,
       or credentials. ``source_ids`` should contain only stable identifiers
       (e.g. ``{"job_id": "...", "resume_version_id": "..."}``).
    """
    return ApplicationFailureEnvelope(
        category=category,
        code=code,
        message=message,
        retryable=retryable,
        next_action=next_action,
        agent_run_id=agent_run_id,
        source_ids=_sanitize_source_ids(source_ids),
        occurred_at=occurred_at or datetime.now(UTC),
    )


def _sanitize_source_ids(source_ids: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``source_ids`` with sensitive text fields removed.

    Only short identifier-like values are kept. Any value longer than a
    reasonable ID length is dropped as a defensive measure against accidentally
    leaking raw text (JD/resume snippets) into the failure envelope.
    """
    if not source_ids:
        return {}
    _MAX_SAFE_VALUE_LEN = 256
    safe: dict[str, Any] = {}
    for key, value in source_ids.items():
        normalized_key = key.lower()
        if any(part in normalized_key for part in _SENSITIVE_SOURCE_ID_KEY_PARTS):
            _log.warning("application.failure_envelope.dropped_sensitive_source_id", key=key)
            continue
        if isinstance(value, str) and len(value) > _MAX_SAFE_VALUE_LEN:
            _log.warning(
                "application.failure_envelope.dropped_long_source_id",
                key=key,
                length=len(value),
            )
            continue
        safe[key] = value
    return safe


# ---------------------------------------------------------------------------
# Duplicate active-operation contract
# ---------------------------------------------------------------------------

#: Agent-run statuses considered "active" (in-flight) for duplicate detection.
_ACTIVE_RUN_STATUSES: frozenset[str] = frozenset({"queued", "running"})


def active_operation_key(
    *,
    operation_type: str,
    application_id: str,
    resume_version_id: str | None,
    source_hash: str,
) -> ActiveOperationKey:
    """Build the identity key used to detect duplicate active operations.

    Two operations sharing this key that are both ``queued`` or ``running``
    are duplicates. Callers (the application records center, artifact
    workflows) should:

    1. Before enqueuing, check for an existing active run with the same key.
    2. If one exists, return the existing run reference (preferred) or raise
       a 409 conflict with the active run id.
    3. Never silently start a second run for the same key.
    """
    return ActiveOperationKey(
        operation_type=operation_type,
        application_id=application_id,
        resume_version_id=resume_version_id,
        source_hash=source_hash,
    )


def is_active_run_status(status: str) -> bool:
    """Return ``True`` if a run ``status`` counts as in-flight for dedup."""
    return status in _ACTIVE_RUN_STATUSES
