"""Application records center service.

This module owns the domain logic for the application lifecycle:

- :func:`create_application` — verify job/resume ownership, detect duplicates,
  and create a new ``ApplicationRecord`` with a ``created`` timeline event.
- :func:`update_application_status` — enforce the state-machine transition
  table, append a ``status_changed`` (or ``failure``) timeline event, and
  persist the latest failure envelope — all in one transaction.
- :func:`append_timeline_note` — append a user-note event without changing
  status.

Ownership rules (design.md §error table):

- job missing or not owned by the current user → ``404 application not found``
  (when accessed via application id) or ``404 job not found`` (on create);
- resume version missing, or its parent resume not owned by the current user
  → ``404 resume version not found``;
- resume version ``raw_text`` empty and the target status is ``preparing`` →
  ``422 resume version has no parsed text``.

Cross-user access returns 404 (not 403) to avoid revealing resource existence,
matching the convention used in ``app/api/v1/jobs.py`` and ``resumes.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import ApplicationRecord, JobPosting, ResumeVersion, UserProfile
from app.db.repositories import application_repo, resume_repo
from app.schemas.application import ApplicationStatus
from app.services.application_state import (
    InvalidTransitionError,
    assert_transition,
)

_log = get_logger("app.services.application_service")

#: Statuses that count as "active" (non-terminal) for duplicate detection.
#: Mirrors the terminal set excluded by ``application_repo.find_duplicate``.
_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        ApplicationStatus.planned.value,
        ApplicationStatus.preparing.value,
        ApplicationStatus.materials_ready.value,
        ApplicationStatus.approval_required.value,
        ApplicationStatus.approved.value,
        ApplicationStatus.failed.value,
        ApplicationStatus.paused.value,
    }
)


def _verify_job_ownership(db: Session, current_user: UserProfile, job_id: str) -> JobPosting:
    """Return the current user's job or raise 404 (not 403)."""
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        _log.info("application.job_not_found", user_id=current_user.id, job_id=job_id)
        raise HTTPException(status_code=404, detail="job not found")
    return job


def _verify_resume_version_ownership(
    db: Session, current_user: UserProfile, resume_version_id: str
) -> ResumeVersion:
    """Return the current user's resume version or raise 404 (not 403)."""
    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        _log.info(
            "application.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        _log.info(
            "application.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
            resume_id=version.resume_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")
    return version


def create_application(
    db: Session,
    current_user: UserProfile,
    *,
    job_id: str,
    resume_version_id: str | None,
) -> ApplicationRecord:
    """Create a new application record for an owned job/resume.

    Verifies job ownership (404), verifies resume version ownership when
    supplied (404), detects duplicates (returns the existing active record),
    and creates a ``planned`` record with a ``created`` timeline event.

    No external platform side effects are performed (PRD: manual-first).
    """
    # 1. Verify job ownership.
    _verify_job_ownership(db, current_user, job_id)

    # 2. Verify resume version ownership when supplied.
    if resume_version_id is not None:
        _verify_resume_version_ownership(db, current_user, resume_version_id)

    # 3. Duplicate detection: if an active record already exists for the same
    #    user/job/resume, return it (PRD: "returns the existing record" path).
    existing = application_repo.find_duplicate(
        db,
        user_id=current_user.id,
        job_id=job_id,
        resume_version_id=resume_version_id,
    )
    if existing is not None:
        _log.info(
            "application.duplicate_found",
            user_id=current_user.id,
            application_id=existing.id,
            job_id=job_id,
        )
        return existing

    # 4. Create the record with a ``created`` timeline event.
    event = application_repo.build_event(
        type="created",
        actor="user",
        to_status=ApplicationStatus.planned.value,
        summary="application record created",
        metadata={"job_id": job_id, "resume_version_id": resume_version_id},
    )
    record = application_repo.create_for_user(
        db,
        user_id=current_user.id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        status=ApplicationStatus.planned.value,
        timeline=[event],
    )
    db.commit()
    db.refresh(record)
    _log.info(
        "application.created",
        user_id=current_user.id,
        application_id=record.id,
        job_id=job_id,
    )
    return record


def update_application_status(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    *,
    new_status: str,
    note: str | None = None,
    failure: dict[str, Any] | None = None,
    agent_run_id: str | None = None,
) -> ApplicationRecord:
    """Enforce the transition table and update the application status.

    Raises ``404`` if the application does not exist or is not owned by the
    current user. Raises ``422`` if the transition is not allowed by the state
    machine. Raises ``422`` if the target status is ``preparing`` and the bound
    resume version has no parsed text (or no resume version is bound).

    The status update and timeline append happen in one transaction (design.md
    Transaction Rule). When ``new_status`` is ``failed`` and ``failure`` is
    supplied, it is persisted on ``latest_error``. When ``agent_run_id`` is
    supplied, it updates ``latest_agent_run_id``.
    """
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "application.not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")

    # Validate the target status is a known enum value.
    try:
        target = ApplicationStatus(new_status)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"unknown status: {new_status}",
        ) from None

    current = ApplicationStatus(record.status)

    # Enforce the transition table.
    try:
        assert_transition(current, target)
    except InvalidTransitionError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"invalid status transition: {current.value} -> {target.value}"
            ),
        ) from None

    # PRD: if the target status is ``preparing``, the bound resume version must
    # have parsed text. A record without a resume version cannot enter
    # ``preparing`` until the user chooses a usable resume.
    if target is ApplicationStatus.preparing:
        _assert_resume_text_for_preparing(db, current_user, record)

    # Build the timeline event and persist status + event together.
    event_type = "failure" if target is ApplicationStatus.failed else "status_changed"
    summary = note or f"status changed from {current.value} to {target.value}"
    if target is ApplicationStatus.failed and failure:
        code = failure.get("code", "unknown")
        summary = f"failed: {code}"

    event = application_repo.build_event(
        type=event_type,
        actor="user",
        from_status=current.value,
        to_status=target.value,
        summary=summary,
        metadata={
            "note": note,
            "agent_run_id": agent_run_id,
        },
    )

    latest_error = failure if target is ApplicationStatus.failed and failure else None

    application_repo.update_status_with_event(
        db,
        record,
        new_status=target.value,
        event=event,
        latest_error=latest_error,
        latest_agent_run_id=agent_run_id,
    )
    db.commit()
    db.refresh(record)
    _log.info(
        "application.status_updated",
        user_id=current_user.id,
        application_id=record.id,
        from_status=current.value,
        to_status=target.value,
    )
    return record


def _assert_resume_text_for_preparing(
    db: Session, current_user: UserProfile, record: ApplicationRecord
) -> None:
    """Raise 422 if the bound resume version has no parsed text (or none bound).

    PRD: "If resume version is missing parsed text, record can exist but cannot
    enter ``preparing`` until the user chooses a usable resume."
    """
    if record.resume_version_id is None:
        raise HTTPException(
            status_code=422,
            detail="cannot enter preparing without a resume version",
        )
    version = _verify_resume_version_ownership(db, current_user, record.resume_version_id)
    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        raise HTTPException(
            status_code=422,
            detail="resume version has no parsed text",
        )


def append_timeline_note(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    *,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> ApplicationRecord:
    """Append a user-note timeline event without changing status.

    Only user-note events may be appended through this path; status-change
    events are owned by :func:`update_application_status` so the timeline never
    claims a transition that did not happen (design.md Transaction Rule).
    """
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "application.not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")

    event = application_repo.build_event(
        type="user_note",
        actor="user",
        summary=summary,
        metadata=metadata or {},
    )
    application_repo.append_timeline_event(db, record, event=event)
    db.commit()
    db.refresh(record)
    return record


def get_application(
    db: Session, current_user: UserProfile, application_id: str
) -> ApplicationRecord:
    """Return the current user's application or raise 404 (not 403)."""
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "application.not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")
    return record


def list_applications(
    db: Session,
    current_user: UserProfile,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[ApplicationRecord], int]:
    """Return ``(rows, total)`` of applications owned by the current user."""
    return application_repo.list_for_user(
        db,
        current_user.id,
        page=page,
        page_size=page_size,
        status=status,
    )
