"""Approval boundary router — planned external actions.

All endpoints are scoped to the current user and to the parent application
record. Cross-user access returns 404 (not 403) to avoid revealing resource
existence, matching the convention used by ``applications.py``.

This task does **not** implement external execution. These endpoints only let
the user preview, approve, revoke, and inspect planned actions. The execution
guard ``app.services.approval_boundary.assert_action_approved`` is the
contract future platform tools must call before touching a platform.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session
from app.db.models.models import UserProfile
from app.schemas.application_action import (
    ApplicationActionCreate,
    ApplicationActionListOut,
    ApplicationActionOut,
)
from app.services import approval_action_service

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post(
    "/{application_id}/actions/preview",
    response_model=ApplicationActionOut,
    status_code=201,
)
def preview_action(
    application_id: str,
    payload: ApplicationActionCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationActionOut:
    """Create a planned external action in ``approval_required`` status.

    The payload hash is computed from the preview so the user later approves
    exactly this payload. No external side effect is performed.
    """
    return approval_action_service.preview_action(
        db, current_user, application_id, payload
    )


@router.post(
    "/{application_id}/actions/{action_id}/approve",
    response_model=ApplicationActionOut,
)
def approve_action(
    application_id: str,
    action_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationActionOut:
    """Approve the exact current payload of a planned action.

    Returns 404 if the application or action does not exist or is not owned by
    the current user. Returns 409 if the action is not in an approvable state.
    """
    return approval_action_service.approve_action(
        db, current_user, application_id, action_id
    )


@router.post(
    "/{application_id}/actions/{action_id}/revoke",
    response_model=ApplicationActionOut,
)
def revoke_action(
    application_id: str,
    action_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationActionOut:
    """Revoke a previously granted approval.

    The action becomes ``revoked`` and the approval record is cleared so a stale
    approval can never be silently reused. No external side effect is performed.
    """
    return approval_action_service.revoke_action(
        db, current_user, application_id, action_id
    )


@router.get(
    "/{application_id}/actions/{action_id}",
    response_model=ApplicationActionOut,
)
def get_action(
    application_id: str,
    action_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationActionOut:
    """Return one planned action (scoped to user + application)."""
    return approval_action_service.get_action(
        db, current_user, application_id, action_id
    )


@router.get(
    "/{application_id}/actions",
    response_model=ApplicationActionListOut,
)
def list_actions(
    application_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationActionListOut:
    """Return all planned actions for an application owned by the current user."""
    items = approval_action_service.list_actions(db, current_user, application_id)
    return ApplicationActionListOut(items=items)
