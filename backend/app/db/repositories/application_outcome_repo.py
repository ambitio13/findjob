"""Repository for the ``ApplicationOutcome`` model.

Append-only outcome events (Phase 1 feedback loop). Query code stays here per
``.trellis/spec/backend/database.md`` Repository Rules; the funnel arithmetic
lives in the metrics service, not in SQL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models.models import ApplicationOutcome, JobAnalysis


def create(
    db: Session,
    *,
    application_id: str,
    user_id: str,
    outcome_type: str,
    source: str,
    occurred_at: datetime,
    evidence: str | None,
) -> ApplicationOutcome:
    row = ApplicationOutcome(
        application_id=application_id,
        user_id=user_id,
        outcome_type=outcome_type,
        source=source,
        occurred_at=occurred_at,
        evidence=evidence,
    )
    db.add(row)
    db.flush()
    return row


def list_by_application(db: Session, application_id: str) -> list[ApplicationOutcome]:
    """Return the outcome events for one application, oldest first."""
    return (
        db.query(ApplicationOutcome)
        .filter(ApplicationOutcome.application_id == application_id)
        .order_by(ApplicationOutcome.occurred_at.asc(), ApplicationOutcome.created_at.asc())
        .all()
    )


def list_for_user(db: Session, user_id: str) -> list[ApplicationOutcome]:
    """Return every outcome event owned by ``user_id`` (funnel input)."""
    return (
        db.query(ApplicationOutcome)
        .filter(ApplicationOutcome.user_id == user_id)
        .order_by(ApplicationOutcome.occurred_at.asc())
        .all()
    )


def latest_match_scores_by_job(db: Session, job_ids: list[str]) -> dict[str, float]:
    """Return ``{job_id: latest match_score}`` for the given jobs.

    Jobs without any analysis (or whose latest analysis has no score) are
    omitted; callers bucket those as ``unknown``.
    """
    if not job_ids:
        return {}
    scores: dict[str, float] = {}
    rows = (
        db.query(JobAnalysis)
        .filter(JobAnalysis.job_id.in_(job_ids), JobAnalysis.match_score.is_not(None))
        .order_by(JobAnalysis.created_at.asc())
        .all()
    )
    for row in rows:
        # Ascending scan keeps overwriting so the latest analysis wins.
        scores[row.job_id] = row.match_score
    return scores
