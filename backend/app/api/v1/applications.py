"""Applications router — application records center.

All endpoints are scoped to the current user: created records bind to
``current_user.id``, and list/detail only return records owned by the current
user. Cross-user access returns 404 (not 403) to avoid revealing resource
existence.

This task is manual-first: no external platform side effects are performed
(PRD). The endpoints cover create/list/detail, status updates (enforcing the
state-machine transition table), and manual timeline notes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session
from app.db.models.models import UserProfile
from app.schemas.api import (
    ApplicationCreate,
    ApplicationListOut,
    ApplicationOut,
    ApplicationStatusUpdate,
    ApplicationTimelineCreate,
    PaginatedMeta,
)
from app.services import application_service

router = APIRouter(prefix="/applications", tags=["applications"])


def _to_out(record) -> ApplicationOut:
    """Project an ``ApplicationRecord`` into ``ApplicationOut``.

    The ``timeline`` column is a JSON list; Pydantic validates each entry into
    an ``ApplicationTimelineEventOut``. A missing/empty timeline degrades to an
    empty list so older rows do not break reads.
    """
    return ApplicationOut.model_validate(record)


@router.get("", response_model=ApplicationListOut)
def list_applications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="Filter by application status."),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationListOut:
    """List the current user's application records, newest first."""
    rows, total = application_service.list_applications(
        db, current_user, page=page, page_size=page_size, status=status
    )
    return ApplicationListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total),
        items=[_to_out(r) for r in rows],
    )


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Create a new application record for an owned job/resume.

    If an active (non-terminal) record already exists for the same job (and,
    when provided, the same resume version), the existing record is returned
    with status 201 so duplicate creation is idempotent from the client's
    perspective (PRD: "returns the existing record").
    """
    record = application_service.create_application(
        db,
        current_user,
        job_id=payload.job_id,
        resume_version_id=payload.resume_version_id,
    )
    return _to_out(record)


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Return one application record (with timeline) for the current user."""
    record = application_service.get_application(db, current_user, application_id)
    return _to_out(record)


@router.patch("/{application_id}/status", response_model=ApplicationOut)
def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Update the application status, enforcing the transition table.

    Returns 404 if the application does not exist or is not owned by the
    current user. Returns 422 if the transition is not allowed or the target
    status is ``preparing`` and the bound resume version has no parsed text.

    When ``status`` is ``failed`` and ``failure`` is supplied, the sanitized
    envelope is persisted on ``latest_error``. When ``agent_run_id`` is
    supplied, it updates ``latest_agent_run_id``.
    """
    record = application_service.update_application_status(
        db,
        current_user,
        application_id,
        new_status=payload.status,
        note=payload.note,
        failure=payload.failure,
        agent_run_id=payload.agent_run_id,
    )
    return _to_out(record)


@router.post("/{application_id}/timeline", response_model=ApplicationOut)
def append_timeline_event(
    application_id: str,
    payload: ApplicationTimelineCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Append a user-note timeline event without changing status.

    Only user-note events may be appended here; status-change events are owned
    by the status-update endpoint so the timeline never claims a transition
    that did not happen (design.md Transaction Rule).
    """
    record = application_service.append_timeline_note(
        db,
        current_user,
        application_id,
        summary=payload.summary,
        metadata=payload.metadata,
    )
    return _to_out(record)
