"""Outcome feedback-loop and funnel-metrics tests (Phase 1).

Covers:

- recording outcomes (manual) on owned applications, 404 on foreign/unknown;
- outcome-driven status transitions when the state machine allows them
  (``submitted`` → ``interviewing`` / ``rejected``) and evidence-only
  recording when it does not;
- outcome listing per application;
- funnel metrics totals, rates, and match-score buckets.

Application rows are seeded directly in the DB (including forced statuses)
because the outcome tests care about state-machine interaction, not about the
preparation pipeline itself.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.models import ApplicationRecord, JobAnalysis, JobPosting, UserProfile
from app.db.repositories import generated_artifact_repo
from app.db.session import SessionLocal

_USER = "outcome_user"
_OTHER = "outcome_other"


def _seed_application(
    db: Session,
    user_id: str,
    *,
    status: str = "submitted",
    match_score: float | None = None,
    opening_prompt_version: str | None = None,
) -> str:
    job = JobPosting(
        user_id=user_id,
        company="Acme",
        title="Backend Engineer",
        jd_raw="Python backend.",
    )
    db.add(job)
    db.flush()
    if match_score is not None:
        db.add(JobAnalysis(job_id=job.id, match_score=match_score))
        db.flush()
    if opening_prompt_version is not None:
        generated_artifact_repo.create(
            db,
            artifact_type="hr_opening_message",
            content="您好，我对这个岗位很感兴趣。",
            user_id=user_id,
            job_id=job.id,
            prompt_version=opening_prompt_version,
        )
    record = ApplicationRecord(user_id=user_id, job_id=job.id, status=status)
    db.add(record)
    db.flush()
    return record.id


def _seed() -> dict[str, str]:
    with SessionLocal() as db:
        db.add(UserProfile(id=_USER, display_name="用户A"))
        db.add(UserProfile(id=_OTHER, display_name="用户B"))
        db.flush()
        app_id = _seed_application(db, _USER, status="submitted", match_score=0.85)
        other_app_id = _seed_application(db, _OTHER, status="submitted")
        db.commit()
        return {"app_id": app_id, "other_app_id": other_app_id}


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


# ---------------------------------------------------------------------------
# Recording outcomes
# ---------------------------------------------------------------------------


def test_record_interview_outcome_transitions_to_interviewing(client: TestClient) -> None:
    ids = _seed()
    resp = client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "interview", "evidence": "HR 约了周三一面"},
        headers=_headers(_USER),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["outcome_type"] == "interview"
    assert body["source"] == "manual"
    assert body["evidence"] == "HR 约了周三一面"

    detail = client.get(f"/api/v1/applications/{ids['app_id']}", headers=_headers(_USER))
    assert detail.json()["status"] == "interviewing"
    # Timeline carries the transition with outcome provenance.
    events = detail.json()["timeline"]
    assert any(
        e["type"] == "status_changed" and e["to_status"] == "interviewing" for e in events
    )


def test_record_rejected_outcome_transitions_to_rejected(client: TestClient) -> None:
    ids = _seed()
    resp = client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "rejected"},
        headers=_headers(_USER),
    )
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/v1/applications/{ids['app_id']}", headers=_headers(_USER))
    assert detail.json()["status"] == "rejected"


def test_reply_outcome_records_without_status_change(client: TestClient) -> None:
    ids = _seed()
    resp = client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "replied"},
        headers=_headers(_USER),
    )
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/v1/applications/{ids['app_id']}", headers=_headers(_USER))
    # ``submitted`` has no lateral transition for a reply — status stays put.
    assert detail.json()["status"] == "submitted"


def test_outcome_on_invalid_transition_is_evidence_only(client: TestClient) -> None:
    ids = _seed()
    # Force the record back to ``planned`` (no path to interviewing).
    with SessionLocal() as db:
        db.execute(
            text("UPDATE application_records SET status='planned' WHERE id=:id"),
            {"id": ids["app_id"]},
        )
        db.commit()

    resp = client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "interview"},
        headers=_headers(_USER),
    )
    assert resp.status_code == 201, resp.text
    detail = client.get(f"/api/v1/applications/{ids['app_id']}", headers=_headers(_USER))
    assert detail.json()["status"] == "planned"  # not forced into interviewing


def test_cross_user_outcome_is_404(client: TestClient) -> None:
    ids = _seed()
    resp = client.post(
        f"/api/v1/applications/{ids['other_app_id']}/outcomes",
        json={"outcome_type": "replied"},
        headers=_headers(_USER),
    )
    assert resp.status_code == 404


def test_unknown_application_outcome_is_404(client: TestClient) -> None:
    _seed()
    resp = client.post(
        "/api/v1/applications/does_not_exist/outcomes",
        json={"outcome_type": "replied"},
        headers=_headers(_USER),
    )
    assert resp.status_code == 404


def test_list_outcomes_oldest_first(client: TestClient) -> None:
    ids = _seed()
    client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "replied", "occurred_at": "2026-08-01T10:00:00+00:00"},
        headers=_headers(_USER),
    )
    client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "interview", "occurred_at": "2026-08-03T10:00:00+00:00"},
        headers=_headers(_USER),
    )
    resp = client.get(
        f"/api/v1/applications/{ids['app_id']}/outcomes", headers=_headers(_USER)
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [o["outcome_type"] for o in items] == ["replied", "interview"]


def test_evidence_length_capped_at_schema_level(client: TestClient) -> None:
    ids = _seed()
    resp = client.post(
        f"/api/v1/applications/{ids['app_id']}/outcomes",
        json={"outcome_type": "replied", "evidence": "x" * 501},
        headers=_headers(_USER),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Funnel metrics
# ---------------------------------------------------------------------------


def test_funnel_metrics_empty_user(client: TestClient) -> None:
    resp = client.get("/api/v1/metrics/funnel", headers=_headers("empty_user"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["applications_total"] == 0
    assert body["reply_rate"] is None
    assert body["by_match_score"] == []


def test_funnel_metrics_rates_and_buckets(client: TestClient) -> None:
    with SessionLocal() as db:
        db.add(UserProfile(id=_USER, display_name="用户A"))
        db.flush()
        # 3 submitted applications: scores 0.9 (replied+interview), 0.7
        # (replied), 0.5 (nothing). Plus one still preparing (excluded from
        # the submitted denominator).
        high = _seed_application(db, _USER, status="submitted", match_score=0.9)
        mid = _seed_application(db, _USER, status="submitted", match_score=0.7)
        _seed_application(db, _USER, status="submitted", match_score=0.5)
        _seed_application(db, _USER, status="preparing", match_score=0.95)
        db.commit()

    h = _headers(_USER)
    client.post(
        f"/api/v1/applications/{high}/outcomes", json={"outcome_type": "replied"}, headers=h
    )
    client.post(
        f"/api/v1/applications/{high}/outcomes", json={"outcome_type": "interview"}, headers=h
    )
    client.post(
        f"/api/v1/applications/{mid}/outcomes", json={"outcome_type": "replied"}, headers=h
    )

    resp = client.get("/api/v1/metrics/funnel", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["applications_total"] == 4
    assert body["submitted"] == 3
    assert body["with_reply"] == 2
    assert body["interviews"] == 1
    assert body["reply_rate"] == round(2 / 3, 4)
    assert body["interview_rate"] == round(1 / 3, 4)

    buckets = {b["bucket"]: b for b in body["by_match_score"]}
    assert buckets[">=0.8"]["applications"] == 1
    assert buckets[">=0.8"]["replied"] == 1
    assert buckets[">=0.8"]["interviews"] == 1
    assert buckets["0.6-0.8"]["replied"] == 1
    assert buckets["<0.6"]["replied"] == 0


def test_funnel_metrics_scoped_to_current_user(client: TestClient) -> None:
    ids = _seed()
    # Record an outcome for the OTHER user's application; the current user's
    # funnel must not see it.
    client.post(
        f"/api/v1/applications/{ids['other_app_id']}/outcomes",
        json={"outcome_type": "replied"},
        headers=_headers(_OTHER),
    )
    resp = client.get("/api/v1/metrics/funnel", headers=_headers(_USER))
    body = resp.json()
    assert body["submitted"] == 1
    assert body["with_reply"] == 0


def test_funnel_metrics_buckets_by_opening_prompt(client: TestClient) -> None:
    """Reply rates by opening-message prompt version (calibration view)."""
    with SessionLocal() as db:
        db.add(UserProfile(id=_USER, display_name="用户A"))
        db.flush()
        v1_replied = _seed_application(
            db, _USER, status="submitted", opening_prompt_version="opening_v1"
        )
        _seed_application(
            db, _USER, status="submitted", opening_prompt_version="opening_v1"
        )
        v2_interview = _seed_application(
            db, _USER, status="submitted", opening_prompt_version="opening_v2"
        )
        # No opening generated at all → "unknown" bucket.
        _seed_application(db, _USER, status="submitted")
        db.commit()

    h = _headers(_USER)
    client.post(
        f"/api/v1/applications/{v1_replied}/outcomes",
        json={"outcome_type": "replied"},
        headers=h,
    )
    client.post(
        f"/api/v1/applications/{v2_interview}/outcomes",
        json={"outcome_type": "interview"},
        headers=h,
    )

    resp = client.get("/api/v1/metrics/funnel", headers=h)
    assert resp.status_code == 200
    buckets = {b["prompt_version"]: b for b in resp.json()["by_opening_prompt"]}
    assert buckets["opening_v1"]["applications"] == 2
    assert buckets["opening_v1"]["replied"] == 1
    assert buckets["opening_v1"]["interviews"] == 0
    assert buckets["opening_v2"]["applications"] == 1
    assert buckets["opening_v2"]["replied"] == 1  # interview implies reply
    assert buckets["opening_v2"]["interviews"] == 1
    assert buckets["unknown"]["applications"] == 1
    assert buckets["unknown"]["replied"] == 0
