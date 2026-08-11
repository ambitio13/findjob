"""Repository for the ``ThresholdCalibration`` model (Phase 5 feedback loop).

Append-only calibration history: each scan that finds enough outcome evidence
adds one row; the newest row per user is the active communicate-gate
threshold.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.models import ThresholdCalibration


def create(
    db: Session,
    *,
    user_id: str,
    threshold: float,
    method: str,
    sample_count: int,
    details: dict[str, Any] | None = None,
) -> ThresholdCalibration:
    """Insert one calibration row and return it (not yet committed)."""
    row = ThresholdCalibration(
        user_id=user_id,
        threshold=threshold,
        method=method,
        sample_count=sample_count,
        details=details,
    )
    db.add(row)
    db.flush()
    return row


def latest_for_user(db: Session, user_id: str) -> ThresholdCalibration | None:
    """Return the user's newest calibration row, or ``None`` when no scan has
    produced one yet (callers then fall back to the module default)."""
    return (
        db.execute(
            select(ThresholdCalibration)
            .where(ThresholdCalibration.user_id == user_id)
            .order_by(ThresholdCalibration.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
