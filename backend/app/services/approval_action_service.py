"""Service layer for the external-action approval boundary.

Owns the domain logic for previewing, approving, revoking, and reading planned
external actions. The service:

- verifies application ownership before any action is created or mutated (404
  on missing / cross-user access, never 403 — matching the convention in
  ``application_service``);
- computes the payload hash via ``approval_boundary.compute_payload_hash`` so
  approval is bound to the exact preview;
- appends a timeline event to the parent ``ApplicationRecord`` for every
  preview/approve/revoke/stale transition so the audit trail explains every
  approval boundary change (per ``authentication.md`` audit-log rules).

This task does **not** implement external execution. The guard
``approval_boundary.assert_action_approved`` is the contract future platform
tools must call; this service only prepares actions for that guard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import ApplicationRecord, UserProfile
from app.db.repositories import application_action_repo, application_repo
from app.db.repositories.application_action_repo import CLEAR
from app.schemas.application_action import (
    ApplicationActionCreate,
    ApplicationActionOut,
    ApplicationActionPreview,
    ApplicationActionSourceSnapshot,
    ApprovalRecord,
    ExternalActionResult,
    ExternalActionResultStatus,
    ExternalActionStatus,
    ExternalActionType,
)
from app.services.approval_boundary import compute_payload_hash

_log = get_logger("app.services.approval_action_service")


def _to_out(action) -> ApplicationActionOut:
    """Project an ``ApplicationAction`` ORM row into ``ApplicationActionOut``."""
    return ApplicationActionOut.model_validate(action)


def _get_owned_application(
    db: Session, current_user: UserProfile, application_id: str
) -> ApplicationRecord:
    """Return the current user's application or raise 404 (not 403)."""
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "approval.application_not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")
    return record


def _append_timeline(
    db: Session,
    record: ApplicationRecord,
    *,
    event_type: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an approval-boundary timeline event without changing status.

    Approval boundary events (preview/approve/revoke/stale) are informational;
    they must not mutate the application status, which is owned by
    ``application_service``. The append and any action mutation share the same
    transaction so the timeline never claims an event that did not happen.
    """
    event = application_repo.build_event(
        type=event_type,
        actor="user",
        to_status=record.status,
        summary=summary,
        metadata=metadata or {},
    )
    application_repo.append_timeline_event(db, record, event=event)


def preview_action(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    payload: ApplicationActionCreate,
) -> ApplicationActionOut:
    """Create a planned external action in ``approval_required`` status.

    Computes the payload hash from the preview, persists the action, and appends
    a ``action_previewed`` timeline event. No external execution happens here.
    """
    record = _get_owned_application(db, current_user, application_id)

    preview = ApplicationActionPreview(
        action_type=payload.action_type,
        target_platform=payload.target_platform,
        target_resource=payload.target_resource,
        selected_artifact_ids=list(payload.selected_artifact_ids),
        outgoing_text=payload.outgoing_text,
        resume_file_reference=payload.resume_file_reference,
    )
    payload_hash = compute_payload_hash(preview)

    action = application_action_repo.create(
        db,
        application_id=application_id,
        user_id=current_user.id,
        action_type=payload.action_type.value,
        status=ExternalActionStatus.approval_required.value,
        payload_preview=preview.model_dump(),
        payload_hash=payload_hash,
        source_snapshot=payload.source_snapshot.model_dump(),
    )

    _append_timeline(
        db,
        record,
        event_type="action_previewed",
        summary=f"action previewed: {payload.action_type.value}",
        metadata={
            "action_id": action.id,
            "action_type": payload.action_type.value,
            "payload_hash": payload_hash,
        },
    )

    db.commit()
    db.refresh(action)
    _log.info(
        "approval.action_previewed",
        user_id=current_user.id,
        application_id=application_id,
        action_id=action.id,
        action_type=payload.action_type.value,
    )
    return _to_out(action)


def approve_action(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    action_id: str,
) -> ApplicationActionOut:
    """Approve the exact current payload of an action.

    The approval records the current ``payload_hash`` (not a client-supplied
    hash) so the user always approves exactly what is stored. If the action is
    not in an approvable state (``approval_required`` or ``stale`` after
    re-preview) a 409 is returned. Revoked actions must be re-previewed first.
    """
    record = _get_owned_application(db, current_user, application_id)

    action = application_action_repo.get_for_user_and_application(
        db,
        action_id=action_id,
        application_id=application_id,
        user_id=current_user.id,
    )
    if action is None:
        _log.info(
            "approval.action_not_found",
            user_id=current_user.id,
            application_id=application_id,
            action_id=action_id,
        )
        raise HTTPException(status_code=404, detail="action not found")

    if action.status not in (
        ExternalActionStatus.approval_required.value,
        ExternalActionStatus.stale.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"action cannot be approved from status {action.status}",
        )

    now = datetime.now(UTC)
    approval_dict = application_action_repo.build_approval_record(
        approved_by=current_user.id,
        approved_at=now,
        approved_payload_hash=action.payload_hash,
    )
    application_action_repo.update(
        db,
        action,
        status=ExternalActionStatus.approved.value,
        approval=approval_dict,
        stale_reason=CLEAR,
    )

    _append_timeline(
        db,
        record,
        event_type="action_approved",
        summary=f"action approved: {action.action_type}",
        metadata={
            "action_id": action.id,
            "action_type": action.action_type,
            "approved_payload_hash": action.payload_hash,
        },
    )

    db.commit()
    db.refresh(action)
    _log.info(
        "approval.action_approved",
        user_id=current_user.id,
        application_id=application_id,
        action_id=action.id,
        action_type=action.action_type,
    )
    return _to_out(action)


def revoke_action(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    action_id: str,
) -> ApplicationActionOut:
    """Revoke a previously granted approval.

    After revocation the action status becomes ``revoked`` and
    ``assert_action_approved`` will block execution. The approval record is
    cleared so a stale approval can never be silently reused.
    """
    record = _get_owned_application(db, current_user, application_id)

    action = application_action_repo.get_for_user_and_application(
        db,
        action_id=action_id,
        application_id=application_id,
        user_id=current_user.id,
    )
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")

    if action.status not in (
        ExternalActionStatus.approved.value,
        ExternalActionStatus.approval_required.value,
        ExternalActionStatus.stale.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"action cannot be revoked from status {action.status}",
        )

    application_action_repo.update(
        db,
        action,
        status=ExternalActionStatus.revoked.value,
        approval=CLEAR,
        stale_reason=CLEAR,
    )

    _append_timeline(
        db,
        record,
        event_type="action_revoked",
        summary=f"action revoked: {action.action_type}",
        metadata={
            "action_id": action.id,
            "action_type": action.action_type,
        },
    )

    db.commit()
    db.refresh(action)
    _log.info(
        "approval.action_revoked",
        user_id=current_user.id,
        application_id=application_id,
        action_id=action.id,
    )
    return _to_out(action)


def get_action(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    action_id: str,
) -> ApplicationActionOut:
    """Return one planned action (scoped to user + application) or raise 404."""
    _get_owned_application(db, current_user, application_id)

    action = application_action_repo.get_for_user_and_application(
        db,
        action_id=action_id,
        application_id=application_id,
        user_id=current_user.id,
    )
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    return _to_out(action)


def list_actions(
    db: Session,
    current_user: UserProfile,
    application_id: str,
) -> list[ApplicationActionOut]:
    """Return all planned actions for an application owned by the current user."""
    _get_owned_application(db, current_user, application_id)
    rows = application_action_repo.list_for_application(
        db, application_id=application_id, user_id=current_user.id
    )
    return [_to_out(a) for a in rows]


def mark_stale(
    db: Session,
    action,
    *,
    reason: str,
    record: ApplicationRecord | None = None,
    commit: bool = False,
) -> None:
    """Mark an approved action stale (source/payload drift).

    Used by future workflows that detect source-data changes after approval.
    Clears the actionable approval status so ``assert_action_approved`` blocks
    execution, but retains the ``approval`` record for audit. When ``record`` is
    provided a ``action_stale`` timeline event is appended.
    """
    application_action_repo.update(
        db,
        action,
        status=ExternalActionStatus.stale.value,
        stale_reason=reason,
    )
    if record is not None:
        _append_timeline(
            db,
            record,
            event_type="action_stale",
            summary=f"action stale: {action.action_type}",
            metadata={
                "action_id": action.id,
                "action_type": action.action_type,
                "reason": reason,
            },
        )
    if commit:
        db.commit()


__all__ = [
    "approve_action",
    "get_action",
    "list_actions",
    "mark_stale",
    "preview_action",
    "revoke_action",
]

# Re-export schemas for convenience so importers can reach the boundary types
# from a single module.
__all__ += [
    "ApplicationActionCreate",
    "ApplicationActionOut",
    "ApplicationActionPreview",
    "ApplicationActionSourceSnapshot",
    "ApprovalRecord",
    "ExternalActionResult",
    "ExternalActionResultStatus",
    "ExternalActionStatus",
    "ExternalActionType",
]
