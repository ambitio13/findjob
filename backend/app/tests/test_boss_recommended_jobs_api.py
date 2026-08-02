"""API integration tests for ``POST /boss/recommended-jobs/current/inspect``.

Covers:

- Disconnected bridge → ``read_failed`` status (no JD read attempted).
- Connected bridge + valid JD → ``ok`` status, job + application created.
- Connected bridge + sparse JD → ``jd_too_sparse`` status.
- Re-inspect same page → same job returned, ``is_new_job=False``.
- Inspect without ``resume_version_id`` → job created, no application.
- ``read_current_jd`` raises → ``read_failed`` status.

The ``read_current_jd`` call is patched so no real userscript round-trip is
needed. The DB is truncated per test via the shared ``client`` fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import Resume, ResumeVersion, UserProfile
from app.db.session import SessionLocal
from app.platforms.boss.userscript_adapter import UserscriptBossPage
from app.platforms.boss.userscript_channel import get_channel, reset_channel

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str) -> UserProfile:
    user = UserProfile(id=user_id, display_name=f"用户 {user_id}")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session, user_id: str
) -> tuple[Resume, ResumeVersion]:
    resume = Resume(user_id=user_id, filename="r.txt")
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        version_no=1,
        raw_text="张三\nPython 5年 FastAPI",
        parsed_facts={"_parser": "text", "_parser_status": "parsed"},
    )
    db.add(version)
    db.flush()
    return resume, version


def _seed(user_id: str = "boss_api_user") -> dict[str, str]:
    """Seed a user with a resume and return their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        _, version = _make_resume(db, user_id)
        db.commit()
        return {
            "user_id": user_id,
            "resume_version_id": version.id,
        }


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _make_jd_dict(
    *,
    page_url_hash: str = "sha256:abcdef12",
    title: str = "高级前端工程师",
    company: str = "字节跳动",
    description: str = "负责前端架构设计和性能优化。",
) -> dict:
    return {
        "title": title,
        "company": company,
        "location": "北京",
        "salary": "25-50K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "skills": ["React", "TypeScript"],
        "description": description,
        "source_kind": "boss_userscript_read_jd",
        "page_url_hash": page_url_hash,
    }


def _connect_channel() -> None:
    """Reset the channel and mark it as connected via a heartbeat."""
    reset_channel()
    ch = get_channel()
    ch.heartbeat(page_id="tab-test", page_url_hash="sha256:test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_inspect_disconnected_bridge_returns_read_failed(client: TestClient) -> None:
    ids = _seed()
    reset_channel()  # disconnected — no heartbeat

    resp = client.post(
        "/api/v1/boss/recommended-jobs/current/inspect",
        json={"resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspect_status"] == "read_failed"
    assert body["job"] is None
    assert body["application"] is None
    assert body["agent_run_id"] is not None


def test_inspect_valid_jd_creates_job_and_application(client: TestClient) -> None:
    ids = _seed()
    jd = _make_jd_dict()
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=jd
    ):
        resp = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspect_status"] == "ok"
    assert body["job"] is not None
    assert body["application"] is not None
    assert body["is_new_job"] is True
    assert body["is_new_application"] is True
    assert body["job"]["platform"] == "boss"
    assert body["job"]["title"] == "高级前端工程师"
    assert body["application"]["job_id"] == body["job"]["id"]
    assert body["application"]["status"] == "planned"
    assert body["agent_run_id"] is not None


def test_inspect_sparse_jd_returns_jd_too_sparse(client: TestClient) -> None:
    ids = _seed()
    jd = _make_jd_dict(title=None)
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=jd
    ):
        resp = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspect_status"] == "jd_too_sparse"
    assert body["job"] is None
    assert body["application"] is None


def test_inspect_none_jd_returns_read_failed(client: TestClient) -> None:
    ids = _seed()
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=None
    ):
        resp = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspect_status"] == "read_failed"
    assert body["job"] is None


def test_inspect_re_read_same_page_returns_existing_job(client: TestClient) -> None:
    ids = _seed()
    jd = _make_jd_dict()
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=jd
    ):
        resp1 = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=jd
    ):
        resp2 = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    body1 = resp1.json()
    body2 = resp2.json()
    assert body1["is_new_job"] is True
    assert body2["is_new_job"] is False
    assert body2["is_new_application"] is False
    assert body1["job"]["id"] == body2["job"]["id"]
    assert body1["application"]["id"] == body2["application"]["id"]


def test_inspect_without_resume_version_creates_job_only(client: TestClient) -> None:
    ids = _seed()
    jd = _make_jd_dict()
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=jd
    ):
        resp = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={},
            headers=_headers(ids["user_id"]),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspect_status"] == "ok"
    assert body["job"] is not None
    assert body["application"] is None
    assert body["is_new_job"] is True
    assert body["is_new_application"] is False


def test_inspect_read_jd_exception_returns_read_failed(client: TestClient) -> None:
    ids = _seed()
    _connect_channel()
    with patch.object(
        UserscriptBossPage,
        "read_current_jd",
        new_callable=AsyncMock,
        side_effect=RuntimeError("userscript timeout"),
    ):
        resp = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspect_status"] == "read_failed"
    assert body["job"] is None


def test_inspect_creates_agent_run_record(client: TestClient) -> None:
    """The endpoint creates an AgentRun with workflow_type=boss_recommended_job_inspect."""
    from app.db.models.models import AgentRun

    ids = _seed()
    jd = _make_jd_dict()
    _connect_channel()
    with patch.object(
        UserscriptBossPage, "read_current_jd", new_callable=AsyncMock, return_value=jd
    ):
        resp = client.post(
            "/api/v1/boss/recommended-jobs/current/inspect",
            json={"resume_version_id": ids["resume_version_id"]},
            headers=_headers(ids["user_id"]),
        )
    assert resp.status_code == 200
    agent_run_id = resp.json()["agent_run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, agent_run_id)
        assert run is not None
        assert run.workflow_type == "boss_recommended_job_inspect"
        assert run.status == "succeeded"
        assert run.result["inspect_status"] == "ok"
