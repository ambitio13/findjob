"""Repository for the ``FollowUpSuggestion`` model (Phase 5 feedback loop).

Encapsulates persistence so route handlers and services stay free of raw
query code (per ``.trellis/spec/backend/database.md`` Repository Rules).
Suggestions are advisory rows produced by the follow-up scan; resolving them
(actioned / dismissed) only mutates the row itself — external effects still
require the existing approval boundary.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.models import FollowUpSuggestion


def create(
    db: Session,
    *,
    user_id: str,
    application_id: str,
    suggestion_type: str,
    title: str,
    detail: str | None = None,
) -> FollowUpSuggestion:
    """Insert a pending suggestion and return it (not yet committed)."""
    row = FollowUpSuggestion(
        user_id=user_id,
        application_id=application_id,
        suggestion_type=suggestion_type,
        title=title,
        detail=detail,
        status="pending",
    )
    db.add(row)
    db.flush()
    return row


def get_for_user(db: Session, suggestion_id: str, user_id: str) -> FollowUpSuggestion | None:
    """Return one suggestion owned by ``user_id`` (cross-user → ``None``)."""
    row = db.get(FollowUpSuggestion, suggestion_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def list_for_user(
    db: Session,
    user_id: str,
    *,
    status: str | None = None,
) -> list[FollowUpSuggestion]:
    """Return the user's suggestions, newest first; optionally by status."""
    base_filter = FollowUpSuggestion.user_id == user_id
    if status is not None:
        base_filter = base_filter & (FollowUpSuggestion.status == status)
    return (
        db.execute(
            select(FollowUpSuggestion)
            .where(base_filter)
            .order_by(FollowUpSuggestion.created_at.desc())
        )
        .scalars()
        .all()
    )


def has_pending(
    db: Session,
    *,
    user_id: str,
    application_id: str,
    suggestion_type: str,
) -> bool:
    """Whether a ``pending`` suggestion of this type already exists.

    The scan uses this to dedupe: a user never sees the same nudge twice
    while the first one is still unresolved.
    """
    row = (
        db.execute(
            select(FollowUpSuggestion.id)
            .where(
                FollowUpSuggestion.user_id == user_id,
                FollowUpSuggestion.application_id == application_id,
                FollowUpSuggestion.suggestion_type == suggestion_type,
                FollowUpSuggestion.status == "pending",
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    return row is not None


def mark_resolved(
    db: Session,
    row: FollowUpSuggestion,
    *,
    status: str,
    resolved_at: datetime,
) -> FollowUpSuggestion:
    """Move a suggestion to ``actioned``/``dismissed`` and stamp the time."""
    row.status = status
    row.resolved_at = resolved_at
    db.flush()
    return row
