"""API integration tests for the BOSS immediate-communicate endpoints.

Covers:

- ``POST /boss/recommended-jobs/{job_id}/communicate/prepare`` — valid request
  → 201 with correct action type/status/payload_hash/idempotency_key.
- Cross-user job → 404.
- Missing ``resume_version_id`` → 422.
- Missing ``match_artifact_id`` → 422.
- Match decision is skip → 422.
- ``POST /boss/recommended-jobs/{job_id}/communicate/{action_id}/execute`` —
  approved → 200 with ``external_started_at`` set.
- Unapproved → 409 with ``detail.reason == "not_approved"``.
- Idempotent replay → 200, same action, no re-execution.
- Payload hash mismatch → 409 with ``detail.reason == "payload_hash_mismatch"``.
- Cross-user → 404.
- Adapter returns ``failed`` → 200 with ``external_result_status == "failed"``
  and non-empty ``failure_code``.
- Adapter returns ``unknown`` → 200 with ``external_result_status == "unknown"``
  and non-empty ``failure_code``.

The fake model gateway is used by default (``MODEL_PROVIDER=fake`` in
``conftest.py``). The DB is truncated per test via the shared ``client``
fixture.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import (
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "comm_api_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="Communicate API 用户")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session,
    user_id: str,
    raw_text: str = "张三\nPython 5年 FastAPI",
    filename: str = "r.txt",
) -> tuple[Resume, ResumeVersion]:
    resume = Resume(user_id=user_id, filename=filename)
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        version_no=1,
        raw_text=raw_text,
        parsed_facts={"_parser": "text", "_parser_status": "parsed"},
    )
    db.add(version)
    db.flush()
    return resume, version


def _make_job(
    db: Session,
    user_id: str,
    jd_raw: str = "Senior Python backend engineer. Build APIs with FastAPI.",
    external_id: str | None = "sha256:page_url_hash_api",
) -> JobPosting:
    job = JobPosting(
        user_id=user_id,
        company="Acme",
        title="Backend Engineer",
        jd_raw=jd_raw,
        external_id=external_id,
    )
    db.add(job)
    db.flush()
    return job


def _seed(
    user_id: str = "comm_api_user",
    *,
    other_user_id: str = "comm_api_other",
) -> dict[str, str]:
    """Seed a user + resume + version + job, returning their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id)
        job = _make_job(db, user_id)

        _make_user(db, other_user_id)
        other_resume, other_version = _make_resume(db, other_user_id)
        other_job = _make_job(db, other_user_id)

        db.commit()
        return {
            "user_id": user_id,
            "resume_id": resume.id,
            "resume_version_id": version.id,
            "job_id": job.id,
            "other_user_id": other_user_id,
            "other_resume_id": other_resume.id,
            "other_resume_version_id": other_version.id,
            "other_job_id": other_job.id,
        }


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _run_match_api(
    client: TestClient, ids: dict[str, str]
) -> dict[str, Any]:
    """Call the match endpoint via the API and return the response body."""
    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_artifact_directly(
    ids: dict[str, str],
    *,
    decision: str = "communicate",
    score: float = 0.82,
    opening_message: str | None = (
        "您好，我是一名有5年Python后端开发经验的工程师，"
        "对贵司的后端工程师职位非常感兴趣，希望能进一步沟通。"
    ),
    reasons: list[str] | None = None,
    risks: list[str] | None = None,
    missing_requirements: list[str] | None = None,
) -> str:
    """Seed a ``boss_match_decision`` artifact directly (no model call)."""
    if reasons is None:
        reasons = ["Strong Python backend experience"]
    if risks is None:
        risks = ["Kafka experience not mentioned"]
    if missing_requirements is None:
        missing_requirements = []
    content = json.dumps(
        {
            "decision": decision,
            "score": score,
            "reasons": reasons,
            "risks": risks,
            "missing_requirements": missing_requirements,
            "opening_message": opening_message,
        },
        ensure_ascii=False,
    )
    with SessionLocal() as db:
        artifact = GeneratedArtifact(
            artifact_type="boss_match_decision",
            content=content,
            user_id=ids["user_id"],
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            source_ids={
                "workflow_type": "boss_match_decision",
                "job_id": ids["job_id"],
                "resume_version_id": ids["resume_version_id"],
                "user_id": ids["user_id"],
                "safety_downgraded": False,
            },
            prompt_version="boss-match-decision-v1",
            model_name="fake-model",
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        return artifact.id


def _approve_via_api(
    client: TestClient, application_id: str, action_id: str, user_id: str
) -> None:
    """Approve the communicate action via the existing approval endpoint."""
    resp = client.post(
        f"/api/v1/applications/{application_id}/actions/{action_id}/approve",
        headers=_headers(user_id),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Prepare
# ---------------------------------------------------------------------------


def test_prepare_valid_request_returns_201(client: TestClient) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    action = body["action"]
    assert action["action_type"] == "boss_immediate_communicate"
    assert action["status"] == "approval_required"
    assert action["payload_hash"].startswith("sha256:")
    assert action["external_idempotency_key"] is not None
    assert action["external_idempotency_key"].startswith(
        f"{action['application_id']}:boss_immediate_communicate:"
    )
    assert action["approval"] is None
    assert action["payload_preview"]["outgoing_text"] is not None
    assert len(action["payload_preview"]["outgoing_text"]) >= 10
    assert "message" in body


def test_prepare_cross_user_job_returns_404(client: TestClient) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["other_resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404


def test_prepare_missing_resume_version_id_returns_422(client: TestClient) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={"match_artifact_id": artifact_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


def test_prepare_missing_match_artifact_id_returns_422(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


def test_prepare_skip_decision_returns_422(client: TestClient) -> None:
    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids, decision="skip", score=0.25, opening_message=None
    )

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def test_execute_approved_returns_200_with_terminal_result(
    client: TestClient, monkeypatch
) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    prepare_resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    assert prepare_resp.status_code == 201, prepare_resp.text
    action = prepare_resp.json()["action"]
    application_id = action["application_id"]
    action_id = action["id"]

    _approve_via_api(client, application_id, action_id, ids["user_id"])

    # Inject a fake adapter with the communicate_succeeded scenario.
    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_succeeded")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"]["external_started_at"] is not None
    # succeeded → external_result_status == "submitted"
    assert body["action"]["external_result_status"] == "submitted"
    assert "已完成" in body["message"]


def test_execute_unapproved_returns_409_not_approved(client: TestClient) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    prepare_resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    assert prepare_resp.status_code == 201
    action = prepare_resp.json()["action"]
    application_id = action["application_id"]
    action_id = action["id"]

    # Do NOT approve.
    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "not_approved"
    assert detail["action_id"] == action_id


def test_execute_idempotent_replay_returns_200_same_action(
    client: TestClient, monkeypatch
) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    prepare_resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    action = prepare_resp.json()["action"]
    application_id = action["application_id"]
    action_id = action["id"]

    _approve_via_api(client, application_id, action_id, ids["user_id"])

    # Inject a fake adapter so the first execute produces a terminal result.
    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_succeeded")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    # First execute — produces a terminal "submitted" result.
    resp1 = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp1.status_code == 200
    assert resp1.json()["action"]["external_result_status"] == "submitted"

    # Second execute → idempotency replay (same terminal result, no re-call).
    resp2 = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["action"]["id"] == action_id
    assert body2["action"]["external_result_status"] == "submitted"
    # The adapter was called only once (first execute), not on the replay.
    assert len(fake.communicate_calls) == 1
    # The message should indicate idempotency replay.
    assert "幂等" in body2["message"] or "重放" in body2["message"]


def test_execute_payload_hash_mismatch_returns_409(client: TestClient) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    prepare_resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    action = prepare_resp.json()["action"]
    application_id = action["application_id"]
    action_id = action["id"]

    _approve_via_api(client, application_id, action_id, ids["user_id"])

    # Tamper with the stored preview so the recomputed payload hash differs
    # from the approved hash.
    from app.db.repositories import application_action_repo
    from app.schemas.application_action import ApplicationActionPreview
    from app.services.approval_boundary import compute_payload_hash

    with SessionLocal() as db:
        action_row = application_action_repo.get_for_user_and_application(
            db,
            action_id=action_id,
            application_id=application_id,
            user_id=ids["user_id"],
        )
        assert action_row is not None
        tampered = dict(action_row.payload_preview)
        tampered["outgoing_text"] = "TAMPERED: completely different message text"
        new_preview = ApplicationActionPreview.model_validate(tampered)
        new_hash = compute_payload_hash(new_preview)
        application_action_repo.update(
            db,
            action_row,
            payload_preview=tampered,
            payload_hash=new_hash,
        )
        db.commit()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "payload_hash_mismatch"


def test_execute_cross_user_returns_404(client: TestClient) -> None:
    ids = _seed()
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    prepare_resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    action = prepare_resp.json()["action"]
    application_id = action["application_id"]
    action_id = action["id"]

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Execute — failed / unknown terminal outcomes
# ---------------------------------------------------------------------------

def _prepare_and_approve(
    client: TestClient, ids: dict[str, str]
) -> tuple[str, str]:
    """Run match → prepare → approve, returning (application_id, action_id)."""
    match = _run_match_api(client, ids)
    artifact_id = match["artifact_id"]

    prepare_resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/prepare",
        json={
            "resume_version_id": ids["resume_version_id"],
            "match_artifact_id": artifact_id,
        },
        headers=_headers(ids["user_id"]),
    )
    assert prepare_resp.status_code == 201, prepare_resp.text
    action = prepare_resp.json()["action"]
    application_id = action["application_id"]
    action_id = action["id"]

    _approve_via_api(client, application_id, action_id, ids["user_id"])
    return application_id, action_id


def test_execute_failed_returns_200_with_failed_status(
    client: TestClient, monkeypatch
) -> None:
    """When the adapter returns ``failed``, the API responds 200 with
    ``external_result_status == "failed"`` and a non-empty ``failure_code``
    inside ``external_result.result``."""
    ids = _seed()
    application_id, action_id = _prepare_and_approve(client, ids)

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_failed")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    action = body["action"]

    # Terminal status is "failed".
    assert action["external_result_status"] == "failed"
    assert action["external_completed_at"] is not None

    # The external_result envelope carries a non-empty failure_code.
    ext_result = action["external_result"]
    assert ext_result is not None
    assert ext_result["result_status"] == "failed"
    failure_code = ext_result["result"].get("failure_code")
    assert failure_code is not None
    assert failure_code != ""


def test_execute_unknown_returns_200_with_unknown_status(
    client: TestClient, monkeypatch
) -> None:
    """When the adapter returns ``unknown``, the API responds 200 with
    ``external_result_status == "unknown"`` and a non-empty ``failure_code``
    inside ``external_result.result``."""
    ids = _seed()
    application_id, action_id = _prepare_and_approve(client, ids)

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_unknown")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/communicate/{action_id}/execute",
        json={"application_id": application_id},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    action = body["action"]

    # Terminal status is "unknown" (hard stop, no retry).
    assert action["external_result_status"] == "unknown"
    assert action["external_completed_at"] is not None

    # The external_result envelope carries a non-empty failure_code.
    ext_result = action["external_result"]
    assert ext_result is not None
    assert ext_result["result_status"] == "unknown"
    failure_code = ext_result["result"].get("failure_code")
    assert failure_code is not None
    assert failure_code != ""
