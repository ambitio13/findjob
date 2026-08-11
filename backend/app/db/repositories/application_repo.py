"""Repository for the ``ApplicationRecord`` model.

Encapsulates persistence so route handlers and services stay free of raw query
code (per ``.trellis/spec/backend/database.md`` Repository Rules). An
``ApplicationRecord`` row tracks one job/resume application through its
lifecycle (planned → preparing → … → submitted/interviewing), with an
append-only ``timeline`` JSON list, the latest failure envelope, latest agent
run provenance, and a readiness-source snapshot.

All read/write functions are scoped to ``user_id`` so cross-user access returns
``None`` (mapped to 404 by the caller) instead of leaking existence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import ApplicationRecord
from app.schemas.application import ApplicationStatus


def create_for_user(
    db: Session,
    *,
    user_id: str,
    job_id: str,
    resume_version_id: str | None = None,
    status: str = ApplicationStatus.planned.value,
    timeline: list[dict[str, Any]] | None = None,
) -> ApplicationRecord:
    """Insert an ``ApplicationRecord`` row and return it (not yet committed).

    The caller is responsible for ownership verification (job + resume version)
    and duplicate detection before calling this function.
    """
    record = ApplicationRecord(
        user_id=user_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        status=status,
        timeline=list(timeline) if timeline else [],
    )
    db.add(record)
    db.flush()
    return record


def get_for_user(db: Session, application_id: str, user_id: str) -> ApplicationRecord | None:
    """Return the ``ApplicationRecord`` for ``application_id`` owned by
    ``user_id``, or ``None``.

    Scopes to ``user_id`` so cross-user access returns ``None`` (mapped to 404
    by the caller) instead of leaking existence.
    """
    record = db.get(ApplicationRecord, application_id)
    if record is None or record.user_id != user_id:
        return None
    return record


def list_for_user(
    db: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[ApplicationRecord], int]:
    """Return ``(rows, total)`` of applications owned by ``user_id``, newest first.

    Optionally filter by ``status``.
    """
    base_filter = ApplicationRecord.user_id == user_id
    if status is not None:
        base_filter = base_filter & (ApplicationRecord.status == status)
    total = db.execute(
        select(func.count()).select_from(ApplicationRecord).where(base_filter)
    ).scalar_one()
    rows = (
        db.execute(
            select(ApplicationRecord)
            .where(base_filter)
            .order_by(ApplicationRecord.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total


def list_all_for_user(db: Session, user_id: str) -> list[ApplicationRecord]:
    """Return every application owned by ``user_id`` (metrics input).

    Unpaged by design: funnel metrics need the full population. A single
    job-seeker's application count is small (order of hundreds), so this is
    never a hot-path concern.
    """
    return (
        db.execute(
            select(ApplicationRecord)
            .where(ApplicationRecord.user_id == user_id)
            .order_by(ApplicationRecord.created_at.desc())
        )
        .scalars()
        .all()
    )


def find_duplicate(
    db: Session,
    *,
    user_id: str,
    job_id: str,
    resume_version_id: str | None,
) -> ApplicationRecord | None:
    """Return an existing non-terminal application for the same user/job/resume,
    or ``None``.

    A "duplicate" is an active (non-terminal) application record that binds the
    same job (and, when provided, the same resume version) for the same user.
    Terminal statuses (``submitted``, ``rejected``, ``interviewing``) are
    excluded so a user can re-apply after a previous attempt concludes.

    When ``resume_version_id`` is ``None``, only records with a NULL
    ``resume_version_id`` are considered duplicates — a later creation with a
    resume version is a distinct attempt, not a duplicate.
    """
    terminal = {
        ApplicationStatus.submitted.value,
        ApplicationStatus.rejected.value,
        ApplicationStatus.interviewing.value,
    }
    base_filter = (
        (ApplicationRecord.user_id == user_id)
        & (ApplicationRecord.job_id == job_id)
        & ApplicationRecord.status.notin_(terminal)
    )
    if resume_version_id is None:
        base_filter = base_filter & (ApplicationRecord.resume_version_id.is_(None))
    else:
        base_filter = base_filter & (
            ApplicationRecord.resume_version_id == resume_version_id
        )
    return (
        db.execute(
            select(ApplicationRecord)
            .where(base_filter)
            .order_by(ApplicationRecord.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def update_status_with_event(
    db: Session,
    record: ApplicationRecord,
    *,
    new_status: str,
    event: dict[str, Any],
    latest_error: dict[str, Any] | None = None,
    latest_agent_run_id: str | None = None,
    readiness_snapshot: dict[str, Any] | None = None,
) -> ApplicationRecord:
    """Apply a status change and append a timeline event in one flush.

    The status update and timeline append are performed together so they share
    the same transaction (design.md Transaction Rule). The caller is
    responsible for verifying the transition is allowed
    (``application_state.assert_transition``) before calling this function.

    ``latest_error`` overwrites the stored failure envelope when the transition
    enters ``failed``; passing ``None`` leaves the existing value untouched
    (callers may clear it explicitly by passing an empty dict if needed in a
    future iteration). ``latest_agent_run_id`` and ``readiness_snapshot`` are
    updated only when non-``None``.
    """
    record.status = new_status

    # Append the event to the timeline JSON list. The column is a JSON list;
    # mutate a copy so SQLAlchemy detects the change.
    current_timeline = list(record.timeline or [])
    current_timeline.append(event)
    record.timeline = current_timeline

    if latest_error is not None:
        record.latest_error = latest_error
    if latest_agent_run_id is not None:
        record.latest_agent_run_id = latest_agent_run_id
    if readiness_snapshot is not None:
        record.readiness_snapshot = readiness_snapshot

    db.flush()
    return record


def append_timeline_event(
    db: Session,
    record: ApplicationRecord,
    *,
    event: dict[str, Any],
) -> ApplicationRecord:
    """Append a timeline event without changing status.

    Used by the manual timeline-note endpoint. Status-change events must go
    through :func:`update_status_with_event` so the timeline never claims a
    transition that did not happen (design.md Transaction Rule).
    """
    current_timeline = list(record.timeline or [])
    current_timeline.append(event)
    record.timeline = current_timeline
    db.flush()
    return record


def new_event_id() -> str:
    """Return a fresh timeline-event identifier."""
    return f"evt_{uuid.uuid4().hex}"


def build_event(
    *,
    type: str,
    actor: str = "user",
    from_status: str | None = None,
    to_status: str | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Build a timeline-event dict in the design.md shape.

    ``id`` is generated here so the event is self-contained before it is
    appended. ``at`` defaults to now (UTC) when omitted.
    """
    from datetime import UTC

    return {
        "id": new_event_id(),
        "type": type,
        "at": (at or datetime.now(UTC)).isoformat(),
        "actor": actor,
        "from_status": from_status,
        "to_status": to_status,
        "summary": summary,
        "metadata": metadata or {},
    }
