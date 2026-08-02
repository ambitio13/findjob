"""External-action approval boundary service.

Owns the domain rules that future platform tools must respect before performing
any external side effect:

- :func:`compute_payload_hash` — stable ``sha256:...`` over a normalized preview.
- :func:`assert_action_approved` — the execution guard. Raises
  :class:`ApprovalBlockedError` (mapped to HTTP 409 by the API) when approval is
  missing, revoked, stale, or bound to a changed payload.
- :func:`check_staleness` — decide whether an approved action is stale given the
  current source hash.

This task does **not** implement external execution. It only prepares the
boundary so that the first automated agent is forced to call
:func:`assert_action_approved` before touching a platform.

See ``.trellis/tasks/08-01-approval-boundary-for-external-actions/`` for the
PRD and design.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger
from app.schemas.application_action import (
    ApplicationActionOut,
    ApplicationActionPreview,
    ApprovalBlockedError,
    ExternalActionStatus,
    ExternalActionType,
)

_log = get_logger("app.services.approval_boundary")

#: Fields hashed in a fixed order for payload-hash stability. Only fields the
#: user is approving are included — never secrets, cookies, tokens, or browser
#: session data.
_HASH_FIELDS: tuple[str, ...] = (
    "action_type",
    "target_platform",
    "target_resource",
    "selected_artifact_ids",
    "outgoing_text",
    "resume_file_reference",
)

_NONE_SENTINEL = "\x00none\x00"


def compute_external_idempotency_key(
    *,
    application_id: str,
    action_type: ExternalActionType,
    payload_hash: str,
) -> str:
    """Return the durable external-action idempotency key.

    Shape: ``{application_id}:{action_type}:{payload_hash}`` (design.md §H1).
    The key binds the platform submit to one exact application, one exact
    action type, and one exact approved payload. The platform submission
    service must persist this key on the action before any platform call and
    check it again before final submit so a retry or re-run after a network
    blip cannot produce a duplicate platform submit.
    """
    return f"{application_id}:{action_type.value}:{payload_hash}"


def _stable_json(value: Any) -> str:
    """Serialize ``value`` into a deterministic JSON string."""
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), default=str)


def _normalize(value: Any) -> Any:
    """Normalize values for deterministic JSON serialization.

    Mirrors ``app.services.application_state._normalize``: ``None`` is mapped to
    a sentinel so it never collides with the string ``"None"``. Lists are sorted
    when they hold plain strings (e.g. artifact ids) so ordering does not affect
    the hash.
    """
    if value is None:
        return _NONE_SENTINEL
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_normalize(v) for v in value]
        if all(isinstance(v, str) for v in items):
            return sorted(items)
        return items
    return value


def compute_payload_hash(preview: ApplicationActionPreview) -> str:
    """Return a stable ``sha256:...`` hash over the action preview.

    The hash covers exactly the fields the user sees and approves: action type,
    target platform/resource, selected artifact ids, outgoing text, and the
    resume file reference. It deliberately excludes secrets, cookies, tokens,
    and browser/session data.
    """
    payload = {field: getattr(preview, field) for field in _HASH_FIELDS}
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def check_staleness(
    *,
    action: ApplicationActionOut,
    current_source_hash: str,
) -> str | None:
    """Return a stale reason string if the action is stale, else ``None``.

    An action is stale when its recorded source hash no longer matches the
    current readiness source hash — meaning job/resume/profile/artifact data
    changed after the user approved. Staleness is informational here; the hard
    guard lives in :func:`assert_action_approved`.
    """
    if action.source_snapshot.source_hash != current_source_hash:
        return "source data changed since approval"
    return None


def assert_action_approved(
    action: ApplicationActionOut,
    *,
    current_payload_hash: str | None = None,
    current_source_hash: str | None = None,
) -> None:
    """Guard that future external execution must call before doing anything.

    Raises :class:`ApprovalBlockedError` when:

    - the action status is not ``approved`` (missing/revoked/stale/blocked);
    - ``current_payload_hash`` is provided and differs from the approved hash
      (payload drifted → reapproval required);
    - ``current_source_hash`` is provided and differs from the action's source
      hash (source data changed → reapproval required).

    On success the function returns ``None``; the caller may proceed with the
    external action. This function never performs the external action itself.
    """
    action_id = action.id

    if action.status is not ExternalActionStatus.approved:
        _log.warning(
            "approval.execution_blocked",
            action_id=action_id,
            status=action.status.value,
        )
        reason = _reason_for_status(action.status)
        raise ApprovalBlockedError(
            reason=reason,
            message=_message_for_status(action.status),
            action_id=action_id,
            next_action=_next_action_for_status(action.status),
        )

    approval = action.approval
    if approval is None:
        # Defensive: status says approved but no approval record exists.
        _log.error("approval.missing_approval_record", action_id=action_id)
        raise ApprovalBlockedError(
            reason="missing_approval",
            message="approval record is missing",
            action_id=action_id,
        )

    if current_payload_hash is not None and current_payload_hash != approval.approved_payload_hash:
        _log.warning(
            "approval.payload_hash_mismatch",
            action_id=action_id,
            approved=approval.approved_payload_hash,
            current=current_payload_hash,
        )
        raise ApprovalBlockedError(
            reason="payload_hash_mismatch",
            message="action payload changed since approval",
            current_payload_hash=current_payload_hash,
            approved_payload_hash=approval.approved_payload_hash,
            action_id=action_id,
        )

    if (
        current_source_hash is not None
        and current_source_hash != action.source_snapshot.source_hash
    ):
        _log.warning(
            "approval.source_stale",
            action_id=action_id,
            approved=action.source_snapshot.source_hash,
            current=current_source_hash,
        )
        raise ApprovalBlockedError(
            reason="source_stale",
            message="source data changed since approval",
            action_id=action_id,
        )


def _reason_for_status(status: ExternalActionStatus) -> str:
    return {
        ExternalActionStatus.draft: "not_approved",
        ExternalActionStatus.approval_required: "not_approved",
        ExternalActionStatus.stale: "stale",
        ExternalActionStatus.revoked: "revoked",
        ExternalActionStatus.blocked: "blocked",
        ExternalActionStatus.approved: "approved",
    }[status]


def _message_for_status(status: ExternalActionStatus) -> str:
    return {
        ExternalActionStatus.draft: "action has not been approved",
        ExternalActionStatus.approval_required: "action is waiting for approval",
        ExternalActionStatus.stale: "approval is stale",
        ExternalActionStatus.revoked: "approval was revoked",
        ExternalActionStatus.blocked: "action is blocked",
        ExternalActionStatus.approved: "approved",
    }[status]


def _next_action_for_status(
    status: ExternalActionStatus,
) -> ApprovalBlockedError._NextAction:  # type: ignore[name-defined]
    reapprove = ApprovalBlockedError._NextAction.reapprove
    return {
        ExternalActionStatus.draft: reapprove,
        ExternalActionStatus.approval_required: reapprove,
        ExternalActionStatus.stale: reapprove,
        ExternalActionStatus.revoked: reapprove,
        ExternalActionStatus.blocked: ApprovalBlockedError._NextAction.manual_review,
        ExternalActionStatus.approved: reapprove,
    }[status]
