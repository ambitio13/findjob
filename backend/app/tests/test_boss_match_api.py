"""API integration tests for ``POST /boss/recommended-jobs/{job_id}/match``.

Covers:

- Valid request → 200, ``MatchDecisionOut`` with decision + score +
  opening_message, an ``AgentRun`` row persisted with ``workflow_type=
  boss_match_decision``, a ``GeneratedArtifact`` row persisted.
- Cross-user job → 404.
- Missing ``resume_version_id`` in body → 422.
- Empty ``resume_version_id`` → 422.
- Skip scenario (via patched fake gateway) → 200, decision=skip,
  opening_message=None, no safety downgrade message.
- Safety downgrade message present when communicate is downgraded.

The fake model gateway is used by default (``MODEL_PROVIDER=fake`` in
``conftest.py``). The DB is truncated per test via the shared ``client``
fixture.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import (
    AgentRun,
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.models_gateway.fake import _BOSS_MATCH_FAKE_OUTPUTS

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "match_api_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="Match API 用户")
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
) -> JobPosting:
    job = JobPosting(
        user_id=user_id,
        company="Acme",
        title="Backend Engineer",
        jd_raw=jd_raw,
    )
    db.add(job)
    db.flush()
    return job


def _seed(
    user_id: str = "match_api_user",
    *,
    raw_text: str = "张三\nPython 5年 FastAPI",
    jd_raw: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> dict[str, str]:
    """Seed a user + resume + version + job, returning their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id, raw_text)
        job = _make_job(db, user_id, jd_raw=jd_raw)
        db.commit()
        return {
            "user_id": user_id,
            "resume_id": resume.id,
            "resume_version_id": version.id,
            "job_id": job.id,
        }


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_match_valid_request_returns_200(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == ids["job_id"]
    assert body["decision"] in ("communicate", "skip", "needs_review")
    assert 0.0 <= body["score"] <= 1.0
    assert isinstance(body["reasons"], list)
    assert isinstance(body["risks"], list)
    assert isinstance(body["missing_requirements"], list)
    assert body["agent_run_id"] is not None
    assert body["artifact_id"] is not None


def test_match_creates_agent_run_with_correct_workflow_type(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 200
    agent_run_id = resp.json()["agent_run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, agent_run_id)
        assert run is not None
        assert run.workflow_type == "boss_match_decision"
        assert run.status == "succeeded"
        assert run.job_id == ids["job_id"]


def test_match_persists_generated_artifact(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 200
    artifact_id = resp.json()["artifact_id"]

    with SessionLocal() as db:
        artifact = db.get(GeneratedArtifact, artifact_id)
        assert artifact is not None
        assert artifact.artifact_type == "boss_match_decision"
        assert artifact.prompt_version == "boss-match-decision-v1"
        assert artifact.source_ids["job_id"] == ids["job_id"]
        assert artifact.source_ids["workflow_type"] == "boss_match_decision"


def test_match_communicate_has_opening_message(client: TestClient) -> None:
    """The default fake gateway returns a communicate output with an opening message."""
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 200
    body = resp.json()
    # Default fake output is communicate with high score and valid opening message.
    assert body["decision"] == "communicate"
    assert body["opening_message"] is not None
    assert len(body["opening_message"]) >= 10
    # No safety downgrade.
    assert body["message"] is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_match_cross_user_job_returns_404(client: TestClient) -> None:
    ids = _seed()
    # Seed a second user.
    with SessionLocal() as db:
        other = UserProfile(id="other_api_user", display_name="Other")
        db.add(other)
        db.commit()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers("other_api_user"),
    )

    assert resp.status_code == 404


def test_match_missing_resume_version_id_returns_422(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 422


def test_match_empty_resume_version_id_returns_422(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ""},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 422


def test_match_missing_resume_version_returns_404(client: TestClient) -> None:
    ids = _seed()

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": "nonexistent_version"},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 404


def test_match_empty_resume_text_returns_422(client: TestClient) -> None:
    ids = _seed(raw_text="   ")

    resp = client.post(
        f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Skip scenario
# ---------------------------------------------------------------------------


def test_match_skip_scenario_returns_200_with_no_opening_message(
    client: TestClient,
) -> None:
    """When the model returns skip, the response has decision=skip and no message."""
    ids = _seed()

    skip_output = _BOSS_MATCH_FAKE_OUTPUTS["skip"]
    with patch(
        "app.models_gateway.fake._BOSS_MATCH_FAKE_OUTPUTS",
        {
            "communicate": skip_output,
            "skip": _BOSS_MATCH_FAKE_OUTPUTS["skip"],
            "needs_review": _BOSS_MATCH_FAKE_OUTPUTS["needs_review"],
        },
    ):
        resp = client.post(
            f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "skip"
    assert body["opening_message"] is None
    # Skip is never safety-downgraded.
    assert body["message"] is None


# ---------------------------------------------------------------------------
# Safety downgrade message
# ---------------------------------------------------------------------------


def test_match_low_score_downgraded_shows_message(client: TestClient) -> None:
    """When communicate is downgraded to needs_review, the message field is set."""
    ids = _seed()

    low_score_output = {
        "decision": "communicate",
        "score": 0.45,
        "reasons": ["Weak match"],
        "risks": [],
        "missing_requirements": [],
        "opening_message": "您好，我对该职位非常感兴趣，希望能进一步沟通。",
    }
    with patch(
        "app.models_gateway.fake._BOSS_MATCH_FAKE_OUTPUTS",
        {
            "communicate": low_score_output,
            "skip": _BOSS_MATCH_FAKE_OUTPUTS["skip"],
            "needs_review": _BOSS_MATCH_FAKE_OUTPUTS["needs_review"],
        },
    ):
        resp = client.post(
            f"/api/v1/boss/recommended-jobs/{ids['job_id']}/match",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "needs_review"
    assert body["opening_message"] is None
    assert body["message"] is not None
    assert "降级" in body["message"]
