"""Repository for the ``ApplicationAction`` model.

Encapsulates persistence of planned external actions and their approval
boundary so route handlers and services stay free of raw query code (per
``.trellis/spec/backend/database.md`` Repository Rules).

All read/write functions are scoped to ``user_id`` so cross-user access returns
``None`` (mapped to 404 by the caller) instead of leaking existence — matching
the convention used by ``application_repo``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import ApplicationAction

#: Sentinel for "the caller explicitly wants to clear this nullable field".
#: A bare ``None`` means "not provided / skip", matching the convention used by
#: :mod:`application_repo`. Pass ``CLEAR`` to set the column to ``NULL``.
CLEAR: Any = object()


def create(
    db: Session,
    *,
    application_id: str,
    user_id: str,
    action_type: str,
    status: str,
    payload_preview: dict[str, Any],
    payload_hash: str,
    source_snapshot: dict[str, Any],
    approval: dict[str, Any] | None = None,
    stale_reason: str | None = None,
    external_idempotency_key: str | None = None,
) -> ApplicationAction:
    """Insert an ``ApplicationAction`` row and return it (not yet committed)."""
    action = ApplicationAction(
        application_id=application_id,
        user_id=user_id,
        action_type=action_type,
        status=status,
        payload_preview=payload_preview,
        payload_hash=payload_hash,
        source_snapshot=source_snapshot,
        approval=approval,
        stale_reason=stale_reason,
        external_idempotency_key=external_idempotency_key,
    )
    db.add(action)
    db.flush()
    return action


def get_for_user(
    db: Session, action_id: str, user_id: str
) -> ApplicationAction | None:
    """Return the action for ``action_id`` owned by ``user_id``, or ``None``.

    Scopes to ``user_id`` so cross-user access returns ``None`` (mapped to 404
    by the caller) instead of leaking existence.
    """
    action = db.get(ApplicationAction, action_id)
    if action is None or action.user_id != user_id:
        return None
    return action


def get_for_user_and_application(
    db: Session,
    *,
    action_id: str,
    application_id: str,
    user_id: str,
) -> ApplicationAction | None:
    """Return the action scoped to both application and user, or ``None``.

    Used by the approval endpoints so a caller cannot approve/revoke an action
    that belongs to another user's application.
    """
    action = (
        db.execute(
            select(ApplicationAction).where(
                (ApplicationAction.id == action_id)
                & (ApplicationAction.application_id == application_id)
                & (ApplicationAction.user_id == user_id)
            )
        )
        .scalars()
        .first()
    )
    return action


def list_for_application(
    db: Session,
    *,
    application_id: str,
    user_id: str,
) -> list[ApplicationAction]:
    """Return all actions for an application owned by ``user_id``, newest first."""
    return (
        db.execute(
            select(ApplicationAction)
            .where(
                (ApplicationAction.application_id == application_id)
                & (ApplicationAction.user_id == user_id)
            )
            .order_by(ApplicationAction.created_at.desc())
        )
        .scalars()
        .all()
    )


def count_for_application(
    db: Session, application_id: str, user_id: str
) -> int:
    """Return the number of actions for an application owned by ``user_id``."""
    return db.execute(
        select(func.count())
        .select_from(ApplicationAction)
        .where(
            (ApplicationAction.application_id == application_id)
            & (ApplicationAction.user_id == user_id)
        )
    ).scalar_one()


def update(
    db: Session,
    action: ApplicationAction,
    *,
    status: str | None = None,
    approval: dict[str, Any] | None | object = None,
    stale_reason: str | None | object = None,
    payload_preview: dict[str, Any] | None = None,
    payload_hash: str | None = None,
    source_snapshot: dict[str, Any] | None = None,
    external_idempotency_key: str | None = None,
    external_started_at: datetime | None = None,
    external_completed_at: datetime | None = None,
    external_result_status: str | None = None,
    external_result: dict[str, Any] | None = None,
) -> ApplicationAction:
    """Apply updates to ``action``, flush, and return it.

    For most fields a ``None`` argument means "not provided / skip" so callers
    can update the approval without clobbering the payload preview — matching
    the convention used by :mod:`application_repo`.

    The ``approval`` and ``stale_reason`` columns are nullable. To *clear* them
    (set to ``NULL``) pass the :data:`CLEAR` sentinel; a bare ``None`` leaves
    the existing value untouched. This lets ``revoke_action`` explicitly clear
    the approval record so a stale approval can never be silently reused.

    The external-* fields are write-once-then-update audit fields owned by the
    platform submission service; ``None`` leaves them untouched.
    """
    if status is not None:
        action.status = status
    if approval is not None:
        action.approval = None if approval is CLEAR else approval  # type: ignore[comparison-overlap]
    if stale_reason is not None:
        action.stale_reason = None if stale_reason is CLEAR else stale_reason  # type: ignore[comparison-overlap]
    if payload_preview is not None:
        action.payload_preview = payload_preview
    if payload_hash is not None:
        action.payload_hash = payload_hash
    if source_snapshot is not None:
        action.source_snapshot = source_snapshot
    if external_idempotency_key is not None:
        action.external_idempotency_key = external_idempotency_key
    if external_started_at is not None:
        action.external_started_at = external_started_at
    if external_completed_at is not None:
        action.external_completed_at = external_completed_at
    if external_result_status is not None:
        action.external_result_status = external_result_status
    if external_result is not None:
        action.external_result = external_result
    db.flush()
    return action


def new_action_id() -> str:
    """Return a fresh action identifier."""
    return f"act_{uuid.uuid4().hex}"


def build_approval_record(
    *,
    approved_by: str,
    approved_at: datetime,
    approved_payload_hash: str,
) -> dict[str, Any]:
    """Build the approval JSON dict in the :class:`ApprovalRecord` shape."""
    return {
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
        "approved_payload_hash": approved_payload_hash,
    }


def get_terminal_for_idempotency_key(
    db: Session,
    *,
    external_idempotency_key: str,
    user_id: str,
) -> ApplicationAction | None:
    """Return the terminal action for an idempotency key, or ``None``.

    A terminal action is one whose external side effect already produced a
    durable result (``submitted`` / ``duplicate`` / ``unknown`` / ``failed``).
    The platform submission service calls this before any platform call so a
    retry or re-run returns the existing result instead of touching the
    platform a second time (design.md §H1).
    """
    return (
        db.execute(
            select(ApplicationAction).where(
                (ApplicationAction.external_idempotency_key == external_idempotency_key)
                & (ApplicationAction.user_id == user_id)
                & (ApplicationAction.external_result_status.is_not(None))
            )
        )
        .scalars()
        .first()
    )


def get_running_for_idempotency_key(
    db: Session,
    *,
    external_idempotency_key: str,
    user_id: str,
) -> ApplicationAction | None:
    """Return an in-flight action for an idempotency key, or ``None``.

    An in-flight action has the idempotency key set and a recorded start time
    but no terminal result yet. The submit guard blocks a duplicate run when
    this returns a row (design.md §H1).
    """
    return (
        db.execute(
            select(ApplicationAction).where(
                (ApplicationAction.external_idempotency_key == external_idempotency_key)
                & (ApplicationAction.user_id == user_id)
                & (ApplicationAction.external_result_status.is_(None))
                & (ApplicationAction.external_started_at.is_not(None))
            )
        )
        .scalars()
        .first()
    )
