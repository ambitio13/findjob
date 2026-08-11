"""Application outcome service (Phase 1 feedback loop).

Owns the domain rules for recording what happened after a submission:

- ownership checks (cross-user access → 404, never 403);
- append-only outcome events plus a matching timeline event;
- **outcome-driven status transitions**: ``interview``/``offer`` move a
  ``submitted`` record to ``interviewing``; ``rejected`` moves
  ``submitted``/``interviewing`` to ``rejected``. When the state machine does
  not allow the transition (e.g. the user already moved the record on), the
  outcome is still recorded — outcomes are evidence, status is bookkeeping.

The evidence field is capped and treated as a short human summary; chat
content, cookies, and platform session data must never be stored there.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import ApplicationRecord, UserProfile
from app.db.repositories import application_outcome_repo, application_repo
from app.schemas.application import ApplicationStatus
from app.schemas.outcome import OutcomeCreate, OutcomeType
from app.services.application_state import can_transition

_log = get_logger("app.services.outcome_service")

#: Outcome → application status the outcome would ideally imply.
_OUTCOME_TO_STATUS: dict[OutcomeType, ApplicationStatus] = {
    OutcomeType.interview: ApplicationStatus.interviewing,
    OutcomeType.offer: ApplicationStatus.interviewing,
    OutcomeType.rejected: ApplicationStatus.rejected,
}


def _get_owned_record(
    db: Session, current_user: UserProfile, application_id: str
) -> ApplicationRecord:
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "outcome.application_not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")
    return record


def record_outcome(
    db: Session,
    current_user: UserProfile,
    application_id: str,
    payload: OutcomeCreate,
):
    """Record an outcome event and return ``(outcome_row, record)``.

    The outcome row, timeline event, and any status transition are persisted in
    one transaction so the timeline never claims an outcome without the event
    row (design.md Transaction Rule, mirrored from status updates).
    """
    record = _get_owned_record(db, current_user, application_id)

    occurred_at = payload.occurred_at or datetime.now(UTC)
    outcome = application_outcome_repo.create(
        db,
        application_id=record.id,
        user_id=current_user.id,
        outcome_type=payload.outcome_type.value,
        source=payload.source.value,
        occurred_at=occurred_at,
        evidence=payload.evidence,
    )

    # Derive the status transition (if any) and persist event + status
    # together. Outcomes always append a timeline event; the status changes
    # only when the state machine allows it.
    current = ApplicationStatus(record.status)
    target = _OUTCOME_TO_STATUS.get(payload.outcome_type)
    transitioned = False
    if target is not None and current is not target and can_transition(current, target):
        event = application_repo.build_event(
            type="status_changed",
            actor="outcome",
            from_status=current.value,
            to_status=target.value,
            summary=f"outcome {payload.outcome_type.value} recorded",
            metadata={"outcome_id": outcome.id, "source": payload.source.value},
            at=occurred_at,
        )
        application_repo.update_status_with_event(db, record, new_status=target.value, event=event)
        transitioned = True
    else:
        event = application_repo.build_event(
            type="user_note",
            actor="outcome",
            summary=f"outcome {payload.outcome_type.value} recorded",
            metadata={"outcome_id": outcome.id, "source": payload.source.value},
            at=occurred_at,
        )
        application_repo.append_timeline_event(db, record, event=event)

    db.commit()
    db.refresh(record)
    _log.info(
        "outcome.recorded",
        user_id=current_user.id,
        application_id=record.id,
        outcome_type=payload.outcome_type.value,
        transitioned=transitioned,
    )
    return outcome, record


def list_outcomes(
    db: Session, current_user: UserProfile, application_id: str
) -> list:
    """Return the outcome events for one owned application, oldest first."""
    record = _get_owned_record(db, current_user, application_id)
    return application_outcome_repo.list_by_application(db, record.id)
