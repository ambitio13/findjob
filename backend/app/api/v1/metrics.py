"""Metrics router — user-scoped funnel metrics (Phase 1 feedback loop).

Read-only aggregates over the current user's applications and outcome
events. Scoped exactly like every other user resource: the dependency
resolves the current user and the service filters on ``user_id``; there is no
cross-user view.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents.opening_message_guard import COMMUNICATE_MIN_SCORE
from app.api.deps import get_current_user, get_db_session
from app.db.models.models import UserProfile
from app.db.repositories import threshold_calibration_repo
from app.schemas.followup import MatchThresholdOut
from app.schemas.outcome import FunnelMetricsOut
from app.services import metrics_service

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/funnel", response_model=FunnelMetricsOut)
def get_funnel_metrics(
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> FunnelMetricsOut:
    """Return the submission funnel: totals, reply/interview rates, and
    per-match-score buckets used to calibrate the communicate gate."""
    return metrics_service.funnel_metrics(db, current_user)


@router.get("/match-threshold-calibration", response_model=MatchThresholdOut)
def get_match_threshold_calibration(
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> MatchThresholdOut:
    """Return the active communicate-gate threshold and its provenance.

    ``source="calibrated"`` when Phase 5 has derived a threshold from the
    user's outcome data (statistics included); ``source="default"`` while the
    module default is still in force.
    """
    latest = threshold_calibration_repo.latest_for_user(db, current_user.id)
    if latest is None:
        return MatchThresholdOut(threshold=COMMUNICATE_MIN_SCORE, source="default")
    return MatchThresholdOut(
        threshold=latest.threshold,
        source="calibrated",
        method=latest.method,
        sample_count=latest.sample_count,
        details=latest.details,
        calibrated_at=latest.created_at,
    )
