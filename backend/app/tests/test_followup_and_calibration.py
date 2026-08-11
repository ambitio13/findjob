"""Follow-up suggestions + threshold calibration tests (Phase 5).

Covers:

- :class:`OutcomeReflector` pure statistics (min-samples gate, low-rate gate,
  unknown-direction collapse, worst-first ordering);
- suggestion scan rules A (stale submission), B (reply → skill-gap plan),
  C (reflector direction advice) plus pending-dedup;
- threshold calibration percentile math, clamping, and minimum-sample guard;
- API endpoints: list / scan / dismiss / action / calibration view.

Application rows are seeded directly (including timelines and forced
statuses) because these tests exercise the feedback loop, not the
preparation pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agents.opening_message_guard import COMMUNICATE_MIN_SCORE
from app.agents.reflector import OutcomeReflector, OutcomeSample
from app.db.models.models import ApplicationRecord, JobAnalysis, JobPosting, UserProfile
from app.db.repositories import application_outcome_repo, generated_artifact_repo
from app.db.session import SessionLocal
from app.services import followup_service

_USER = "followup_user"
_OTHER = "followup_other"


def _seed_job(
    db: Session, user_id: str, *, direction: str | None = None, match_score: float | None = None
) -> str:
    job = JobPosting(
        user_id=user_id,
        company="Acme",
        title="Backend Engineer",
        jd_raw="Python backend.",
        direction=direction,
    )
    db.add(job)
    db.flush()
    if match_score is not None:
        db.add(JobAnalysis(job_id=job.id, match_score=match_score))
        db.flush()
    return job.id


def _seed_application(
    db: Session,
    user_id: str,
    job_id: str,
    *,
    status: str = "submitted",
    submitted_at: datetime | None = None,
) -> str:
    timeline = None
    if submitted_at is not None:
        timeline = [
            {
                "id": "evt_seed",
                "type": "status_changed",
                "at": submitted_at.isoformat(),
                "actor": "user",
                "from_status": "approved",
                "to_status": "submitted",
                "summary": "submitted",
                "metadata": {},
            }
        ]
    record = ApplicationRecord(user_id=user_id, job_id=job_id, status=status, timeline=timeline)
    db.add(record)
    db.flush()
    return record.id


def _seed_outcome(
    db: Session, user_id: str, application_id: str, outcome_type: str = "replied"
) -> None:
    application_outcome_repo.create(
        db,
        application_id=application_id,
        user_id=user_id,
        outcome_type=outcome_type,
        source="manual",
        occurred_at=datetime.now(UTC),
        evidence=None,
    )


def _seed_users() -> None:
    with SessionLocal() as db:
        db.add(UserProfile(id=_USER, display_name="用户A"))
        db.add(UserProfile(id=_OTHER, display_name="用户B"))
        db.commit()


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


# ---------------------------------------------------------------------------
# OutcomeReflector — pure statistics
# ---------------------------------------------------------------------------


def test_reflector_flags_low_reply_direction() -> None:
    samples = [OutcomeSample(direction="backend", replied=False)] * 5
    result = OutcomeReflector().reflect(samples)
    assert len(result.low_reply_directions) == 1
    stat = result.low_reply_directions[0]
    assert stat.direction == "backend"
    assert stat.submitted == 5
    assert stat.replied == 0
    assert stat.reply_rate == 0.0


def test_reflector_requires_min_samples() -> None:
    samples = [OutcomeSample(direction="backend", replied=False)] * 4
    assert OutcomeReflector().reflect(samples).low_reply_directions == []


def test_reflector_requires_rate_below_threshold() -> None:
    # 1 reply among 5 → 0.2 ≥ 0.10 → not flagged.
    samples = [OutcomeSample(direction="backend", replied=True)] + [
        OutcomeSample(direction="backend", replied=False)
    ] * 4
    assert OutcomeReflector().reflect(samples).low_reply_directions == []


def test_reflector_collapses_blank_direction_and_sorts_worst_first() -> None:
    samples = (
        [OutcomeSample(direction="", replied=False)] * 5
        + [OutcomeSample(direction="frontend", replied=False)] * 6
        + [OutcomeSample(direction="frontend", replied=True)]  # 1/7 ≈ 0.143 → safe
    )
    result = OutcomeReflector().reflect(samples)
    directions = [s.direction for s in result.low_reply_directions]
    # blank collapses to ``unknown``; frontend is above the 0.10 bar.
    assert directions == ["unknown"]


def test_reflector_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        OutcomeReflector(min_samples=0)
    with pytest.raises(ValueError):
        OutcomeReflector(low_reply_rate=1.5)


# ---------------------------------------------------------------------------
# Suggestion scan rules
# ---------------------------------------------------------------------------


def test_rule_a_stale_submission_suggests_new_opening(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        app_id = _seed_application(
            db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(days=4)
        )
        db.commit()

    with SessionLocal() as db:
        rows = followup_service.generate_suggestions_for_user(db, _USER)
        db.commit()
    assert len(rows) == 1
    assert rows[0].suggestion_type == "change_opening_message"
    assert rows[0].application_id == app_id
    assert rows[0].status == "pending"


def test_rule_a_fresh_submission_gets_no_suggestion(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        _seed_application(db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(hours=2))
        db.commit()

    with SessionLocal() as db:
        assert followup_service.generate_suggestions_for_user(db, _USER) == []
        db.rollback()


def test_rule_a_skips_when_hr_replied(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        app_id = _seed_application(
            db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(days=9)
        )
        _seed_outcome(db, _USER, app_id, "replied")
        db.commit()

    with SessionLocal() as db:
        rows = followup_service.generate_suggestions_for_user(db, _USER)
        db.commit()
    # Replied → rule B fires instead of rule A.
    assert [r.suggestion_type for r in rows] == ["skill_gap_plan"]


def test_rule_b_embeds_skill_gaps_from_latest_jd_analysis(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        app_id = _seed_application(db, _USER, job_id)
        generated_artifact_repo.create(
            db,
            artifact_type="jd_analysis",
            content=json.dumps({"skill_gaps": ["Kubernetes", "gRPC"]}),
            user_id=_USER,
            job_id=job_id,
        )
        _seed_outcome(db, _USER, app_id, "replied")
        db.commit()

    with SessionLocal() as db:
        rows = followup_service.generate_suggestions_for_user(db, _USER)
        db.commit()
    assert len(rows) == 1
    assert rows[0].suggestion_type == "skill_gap_plan"
    assert "Kubernetes" in rows[0].detail
    assert "gRPC" in rows[0].detail


def test_scan_dedupes_pending_suggestions(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        _seed_application(db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(days=5))
        db.commit()

    with SessionLocal() as db:
        assert len(followup_service.generate_suggestions_for_user(db, _USER)) == 1
        db.commit()
    with SessionLocal() as db:
        # Second scan: the pending suggestion blocks a duplicate.
        assert followup_service.generate_suggestions_for_user(db, _USER) == []
        db.rollback()


def test_rule_c_nudges_active_applications_in_weak_direction(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        # 5 submitted apps in direction ``backend`` with zero replies.
        for _ in range(5):
            job_id = _seed_job(db, _USER, direction="backend")
            app_id = _seed_application(db, _USER, job_id)
            _seed_outcome(db, _USER, app_id, "rejected")
        # One still-active application in the same direction gets the nudge.
        active_job = _seed_job(db, _USER, direction="backend")
        active_app = _seed_application(db, _USER, active_job, status="materials_ready")
        # An application in another direction must NOT be nudged.
        safe_job = _seed_job(db, _USER, direction="frontend")
        _seed_application(db, _USER, safe_job, status="materials_ready")
        db.commit()

    with SessionLocal() as db:
        rows = followup_service.generate_suggestions_for_user(db, _USER)
        db.commit()
    nudges = [r for r in rows if r.suggestion_type == "low_reply_rate_direction"]
    assert len(nudges) == 1
    assert nudges[0].application_id == active_app
    assert "backend" in nudges[0].title


# ---------------------------------------------------------------------------
# Threshold calibration
# ---------------------------------------------------------------------------


def _seed_calibration_dataset(db: Session) -> None:
    """5 submitted apps with outcomes; replied group scores 70/80."""
    scores = [
        (0.50, "rejected"),
        (0.55, "rejected"),
        (0.60, "rejected"),
        (0.70, "replied"),
        (0.80, "replied"),
    ]
    for score, outcome in scores:
        job_id = _seed_job(db, _USER, match_score=score * 100)
        app_id = _seed_application(db, _USER, job_id)
        _seed_outcome(db, _USER, app_id, outcome)


def test_calibration_computes_clamped_quantile(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        _seed_calibration_dataset(db)
        db.commit()

    with SessionLocal() as db:
        threshold = followup_service.calibrate_match_threshold(db, _USER)
        db.commit()
    # p25 of [70, 80] = 72.5 → 0.725, inside the clamp band.
    assert threshold == 0.725

    with SessionLocal() as db:
        assert followup_service.active_match_threshold(db, _USER) == 0.725


def test_calibration_needs_minimum_samples(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER, match_score=80.0)
        app_id = _seed_application(db, _USER, job_id)
        _seed_outcome(db, _USER, app_id, "replied")
        db.commit()

    with SessionLocal() as db:
        assert followup_service.calibrate_match_threshold(db, _USER) is None
        db.rollback()
    with SessionLocal() as db:
        # No calibration row → default stays in force.
        assert followup_service.active_match_threshold(db, _USER) == COMMUNICATE_MIN_SCORE


def test_calibration_clamps_to_floor(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        # Replied group scored very low → raw p25/100 < 0.4 clamps up.
        for score, outcome in [
            (0.20, "rejected"),
            (0.22, "rejected"),
            (0.25, "rejected"),
            (0.30, "replied"),
            (0.32, "replied"),
        ]:
            job_id = _seed_job(db, _USER, match_score=score * 100)
            app_id = _seed_application(db, _USER, job_id)
            _seed_outcome(db, _USER, app_id, outcome)
        db.commit()

    with SessionLocal() as db:
        threshold = followup_service.calibrate_match_threshold(db, _USER)
        db.commit()
    assert threshold == 0.4


def test_calibration_clamps_to_ceiling(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        # Replied group scored at the top of the scale (incl. the 100
        # boundary) → raw p25/100 > 0.8 must clamp down to the ceiling.
        for score, outcome in [
            (0.50, "rejected"),
            (0.55, "rejected"),
            (0.60, "rejected"),
            (0.95, "replied"),
            (1.00, "replied"),
        ]:
            job_id = _seed_job(db, _USER, match_score=score * 100)
            app_id = _seed_application(db, _USER, job_id)
            _seed_outcome(db, _USER, app_id, outcome)
        db.commit()

    with SessionLocal() as db:
        threshold = followup_service.calibrate_match_threshold(db, _USER)
        db.commit()
    # p25 of [95, 100] = 96.25 → 0.9625, clamped to the 0.8 ceiling.
    assert threshold == 0.8


def test_calibration_replied_score_zero_clamps_to_floor(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        # 0 boundary: the only replied application scored 0.
        for score, outcome in [
            (0.00, "replied"),
            (0.40, "rejected"),
            (0.45, "rejected"),
            (0.50, "rejected"),
            (0.55, "rejected"),
        ]:
            job_id = _seed_job(db, _USER, match_score=score * 100)
            app_id = _seed_application(db, _USER, job_id)
            _seed_outcome(db, _USER, app_id, outcome)
        db.commit()

    with SessionLocal() as db:
        threshold = followup_service.calibrate_match_threshold(db, _USER)
        db.commit()
    # p25 of [0] = 0 → 0.0, clamped up to the 0.4 floor.
    assert threshold == 0.4


def test_calibration_without_replied_group_keeps_default(client: TestClient) -> None:
    _ = client  # fixture: truncates tables before the test
    _seed_users()
    with SessionLocal() as db:
        # Enough samples but every outcome is a rejection → empty replied
        # group: calibration must decline and the default stays in force.
        for score in [0.50, 0.55, 0.60, 0.65, 0.70]:
            job_id = _seed_job(db, _USER, match_score=score * 100)
            app_id = _seed_application(db, _USER, job_id)
            _seed_outcome(db, _USER, app_id, "rejected")
        db.commit()

    with SessionLocal() as db:
        assert followup_service.calibrate_match_threshold(db, _USER) is None
        db.rollback()
    with SessionLocal() as db:
        assert followup_service.active_match_threshold(db, _USER) == COMMUNICATE_MIN_SCORE


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def test_follow_up_scan_and_list_endpoints(client: TestClient) -> None:
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        _seed_application(db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(days=4))
        db.commit()

    resp = client.post("/api/v1/applications/follow-up-scan", headers=_headers(_USER))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["suggestions"][0]["suggestion_type"] == "change_opening_message"
    assert body["active_threshold"] == COMMUNICATE_MIN_SCORE
    assert body["calibrated_now"] is False

    listing = client.get(
        "/api/v1/applications/follow-up-suggestions", headers=_headers(_USER)
    )
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    # Cross-user isolation: the other user sees nothing.
    other = client.get(
        "/api/v1/applications/follow-up-suggestions", headers=_headers(_OTHER)
    )
    assert other.json()["items"] == []


def test_dismiss_and_action_lifecycle(client: TestClient) -> None:
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        _seed_application(db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(days=4))
        db.commit()
    scan = client.post("/api/v1/applications/follow-up-scan", headers=_headers(_USER))
    suggestion_id = scan.json()["suggestions"][0]["id"]

    dismissed = client.post(
        f"/api/v1/applications/follow-up-suggestions/{suggestion_id}/dismiss",
        headers=_headers(_USER),
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["resolved_at"] is not None

    # Resolving twice is rejected.
    again = client.post(
        f"/api/v1/applications/follow-up-suggestions/{suggestion_id}/action",
        headers=_headers(_USER),
    )
    assert again.status_code == 409

    # Unknown id → 404; cross-user id → 404.
    missing = client.post(
        "/api/v1/applications/follow-up-suggestions/nope/dismiss",
        headers=_headers(_USER),
    )
    assert missing.status_code == 404
    foreign = client.post(
        f"/api/v1/applications/follow-up-suggestions/{suggestion_id}/dismiss",
        headers=_headers(_OTHER),
    )
    assert foreign.status_code == 404


def test_action_endpoint_marks_actioned(client: TestClient) -> None:
    _seed_users()
    with SessionLocal() as db:
        job_id = _seed_job(db, _USER)
        _seed_application(db, _USER, job_id, submitted_at=datetime.now(UTC) - timedelta(days=4))
        db.commit()
    scan = client.post("/api/v1/applications/follow-up-scan", headers=_headers(_USER))
    suggestion_id = scan.json()["suggestions"][0]["id"]

    actioned = client.post(
        f"/api/v1/applications/follow-up-suggestions/{suggestion_id}/action",
        headers=_headers(_USER),
    )
    assert actioned.status_code == 200, actioned.text
    assert actioned.json()["status"] == "actioned"


def test_match_threshold_calibration_endpoint(client: TestClient) -> None:
    _seed_users()
    # No calibration yet → default.
    resp = client.get(
        "/api/v1/metrics/match-threshold-calibration", headers=_headers(_USER)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "threshold": COMMUNICATE_MIN_SCORE,
        "source": "default",
        "method": None,
        "sample_count": None,
        "details": None,
        "calibrated_at": None,
    }

    with SessionLocal() as db:
        _seed_calibration_dataset(db)
        db.commit()
    scan = client.post("/api/v1/applications/follow-up-scan", headers=_headers(_USER))
    assert scan.json()["calibrated_now"] is True
    assert scan.json()["active_threshold"] == 0.725

    calibrated = client.get(
        "/api/v1/metrics/match-threshold-calibration", headers=_headers(_USER)
    )
    body = calibrated.json()
    assert body["source"] == "calibrated"
    assert body["threshold"] == 0.725
    assert body["sample_count"] == 5
    assert body["details"]["replied_samples"] == 2
