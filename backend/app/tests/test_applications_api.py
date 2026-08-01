"""API integration tests for the application records center.

Covers the acceptance criteria in the PRD:

- ``GET /applications`` returns paginated current-user records.
- ``POST /applications`` creates a record for an owned job/resume.
- ``GET /applications/{id}`` returns detail with timeline.
- Status update endpoint enforces the transition table (422 on invalid).
- Timeline events explain create/update transitions.
- Cross-user access returns 404 (not 403).
- Duplicate creation returns the existing record.
- No external platform side effects are implemented (manual-first).

The test DB is a real PostgreSQL instance (see ``conftest.py``). Each test gets
a truncated schema. The model gateway uses the fake provider; these tests never
touch the model layer — the application records center is pure CRUD + state
machine.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import JobPosting, Resume, ResumeVersion, UserProfile
from app.db.session import SessionLocal

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str) -> UserProfile:
    user = UserProfile(id=user_id, display_name=f"用户 {user_id}")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session, user_id: str, raw_text: str = "张三\nPython 5年 FastAPI"
) -> tuple[Resume, ResumeVersion]:
    resume = Resume(user_id=user_id, filename="r.txt")
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


def _make_job(db: Session, user_id: str) -> JobPosting:
    job = JobPosting(
        user_id=user_id,
        company="Acme",
        title="Backend Engineer",
        jd_raw="Senior Python backend engineer.",
    )
    db.add(job)
    db.flush()
    return job


def _seed(
    user_id: str = "app_user",
    *,
    other_user_id: str = "app_other",
    raw_text: str = "张三\nPython 5年 FastAPI",
) -> dict[str, str]:
    """Seed two users (one owns a job+resume, the other only a job+resume for
    cross-user tests) and return their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id, raw_text)
        job = _make_job(db, user_id)

        _make_user(db, other_user_id)
        other_resume, other_version = _make_resume(db, other_user_id, raw_text)
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


# ---------------------------------------------------------------------------
# Create + list + detail
# ---------------------------------------------------------------------------


def test_create_application_happy_path(client: TestClient) -> None:
    ids = _seed()
    resp = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"], "resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["job_id"] == ids["job_id"]
    assert body["resume_version_id"] == ids["resume_version_id"]
    assert body["status"] == "planned"
    assert body["user_id"] == ids["user_id"]
    # The ``created`` timeline event must be present.
    assert len(body["timeline"]) == 1
    event = body["timeline"][0]
    assert event["type"] == "created"
    assert event["to_status"] == "planned"


def test_create_application_without_resume(client: TestClient) -> None:
    """A record may start in ``planned`` without a resume version."""
    ids = _seed()
    resp = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"], "resume_version_id": None},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["resume_version_id"] is None
    assert body["status"] == "planned"


def test_list_applications_paginated_and_scoped(client: TestClient) -> None:
    ids = _seed()
    # Create two applications for the main user, one for the other user.
    for _ in range(2):
        client.post(
            "/api/v1/applications",
            json={"job_id": ids["job_id"]},
            headers=_headers(ids["user_id"]),
        )
    # The second+ POST for the same job/resume returns the existing record
    # (duplicate), so only one record exists for the main user. Create a
    # distinct application for the other user to confirm scoping.
    client.post(
        "/api/v1/applications",
        json={"job_id": ids["other_job_id"]},
        headers=_headers(ids["other_user_id"]),
    )

    resp = client.get("/api/v1/applications", headers=_headers(ids["user_id"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["user_id"] == ids["user_id"]


def test_get_application_detail_with_timeline(client: TestClient) -> None:
    ids = _seed()
    create_resp = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    )
    app_id = create_resp.json()["id"]

    resp = client.get(
        f"/api/v1/applications/{app_id}", headers=_headers(ids["user_id"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == app_id
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["type"] == "created"


# ---------------------------------------------------------------------------
# Cross-user 404
# ---------------------------------------------------------------------------


def test_get_application_cross_user_returns_404(client: TestClient) -> None:
    ids = _seed()
    create_resp = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    )
    app_id = create_resp.json()["id"]

    # Access as the other user → 404 (not 403).
    resp = client.get(
        f"/api/v1/applications/{app_id}", headers=_headers(ids["other_user_id"])
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "application not found"


def test_create_application_cross_user_job_returns_404(client: TestClient) -> None:
    ids = _seed()
    # The other user tries to create an application for the main user's job.
    resp = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"


def test_create_application_cross_user_resume_returns_404(client: TestClient) -> None:
    ids = _seed()
    # The other user's job is valid, but the resume version belongs to the
    # main user → 404.
    resp = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["other_job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume version not found"


# ---------------------------------------------------------------------------
# Status update + transition table
# ---------------------------------------------------------------------------


def test_status_update_valid_transition(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    # planned → preparing is allowed.
    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "preparing", "note": "starting prep"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "preparing"
    # A status_changed event must have been appended.
    events = body["timeline"]
    assert any(e["type"] == "status_changed" for e in events)
    last = events[-1]
    assert last["from_status"] == "planned"
    assert last["to_status"] == "preparing"
    assert last["summary"] == "starting prep"


def test_status_update_invalid_transition_returns_422(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    # planned → submitted is NOT allowed (must go through preparing → …).
    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "submitted"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422
    assert "invalid status transition" in resp.json()["detail"]


def test_status_update_unknown_status_returns_422(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "not_a_real_status"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


def test_status_update_cross_user_returns_404(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "preparing"},
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404


def test_status_update_preparing_without_resume_returns_422(client: TestClient) -> None:
    """A record without a resume version cannot enter ``preparing``."""
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "preparing"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422
    assert "resume version" in resp.json()["detail"]


def test_status_update_preparing_with_empty_text_returns_422(client: TestClient) -> None:
    """A record bound to a resume version with no parsed text cannot enter
    ``preparing``."""
    ids = _seed(raw_text="   ")
    app_id = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "preparing"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "resume version has no parsed text"


def test_status_update_failure_persists_latest_error(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    # planned → preparing → failed (allowed transition).
    client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "preparing"},
        headers=_headers(ids["user_id"]),
    )
    failure: dict[str, Any] = {
        "category": "model",
        "code": "model_call_failed",
        "message": "model call failed",
        "retryable": True,
        "next_action": "retry",
        "agent_run_id": "run_abc",
        "source_ids": {"job_id": ids["job_id"]},
        "occurred_at": "2026-08-01T00:00:00Z",
    }
    resp = client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "failed", "failure": failure, "agent_run_id": "run_abc"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["latest_error"] is not None
    assert body["latest_error"]["code"] == "model_call_failed"
    assert body["latest_agent_run_id"] == "run_abc"
    # A failure event must have been appended.
    assert any(e["type"] == "failure" for e in body["timeline"])


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_duplicate_create_returns_existing_record(client: TestClient) -> None:
    ids = _seed()
    first = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    # Second creation for the same job/resume returns the existing record.
    second = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    )
    assert second.status_code == 201
    assert second.json()["id"] == first_id


def test_duplicate_create_without_resume_returns_existing(client: TestClient) -> None:
    ids = _seed()
    first = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    )
    first_id = first.json()["id"]

    second = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    )
    assert second.json()["id"] == first_id


# ---------------------------------------------------------------------------
# Timeline notes
# ---------------------------------------------------------------------------


def test_append_timeline_note(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.post(
        f"/api/v1/applications/{app_id}/timeline",
        json={"summary": "need to update resume projects section"},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    note_events = [e for e in body["timeline"] if e["type"] == "user_note"]
    assert len(note_events) == 1
    assert note_events[0]["summary"] == "need to update resume projects section"


def test_append_timeline_note_cross_user_404(client: TestClient) -> None:
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.post(
        f"/api/v1/applications/{app_id}/timeline",
        json={"summary": "hi"},
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Transactional behavior: status + timeline in one transaction
# ---------------------------------------------------------------------------


def test_status_and_timeline_are_transactional(client: TestClient) -> None:
    """If a status update succeeds, both the new status and the timeline event
    are persisted together — the timeline never claims a transition that did
    not happen (design.md Transaction Rule).

    We verify by reading the row directly from the DB after a successful
    transition and asserting the status + last timeline event agree.
    """
    from app.db.models.models import ApplicationRecord

    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    client.patch(
        f"/api/v1/applications/{app_id}/status",
        json={"status": "preparing"},
        headers=_headers(ids["user_id"]),
    )

    with SessionLocal() as db:
        record = db.get(ApplicationRecord, app_id)
        assert record is not None
        assert record.status == "preparing"
        timeline = record.timeline or []
        assert len(timeline) == 2  # created + status_changed
        last = timeline[-1]
        assert last["type"] == "status_changed"
        assert last["from_status"] == "planned"
        assert last["to_status"] == "preparing"


# ---------------------------------------------------------------------------
# Readiness artifacts listing (GET /applications/{id}/artifacts)
# ---------------------------------------------------------------------------


def _persist_readiness_artifact(
    db: Session,
    *,
    application_id: str,
    job_id: str,
    user_id: str,
    resume_version_id: str,
    artifact_type: str = "hr_opening_message",
    content: str = "{}",
    source_ids: dict[str, Any] | None = None,
) -> None:
    """Insert a readiness ``GeneratedArtifact`` row directly.

    The readiness worker normally writes this after a successful run. Tests
    bypass the worker and persist rows manually to exercise the listing query
    (which scopes by ``job_id`` + ``source_ids['application_id']``).
    """
    from app.db.models.models import GeneratedArtifact

    if source_ids is None:
        source_ids = {"application_id": application_id}
    db.add(
        GeneratedArtifact(
            artifact_type=artifact_type,
            content=content,
            user_id=user_id,
            job_id=job_id,
            resume_version_id=resume_version_id,
            agent_run_id=None,
            source_ids=source_ids,
            prompt_version="v1",
            model_name="fake-model",
        )
    )
    db.flush()


def test_list_application_artifacts_returns_scoped_rows(client: TestClient) -> None:
    """The endpoint returns only readiness artifacts bound to this application.

    We seed two readiness artifacts for the application plus one ``jd_analysis``
    artifact for the same job (which must be excluded) and one readiness
    artifact bound to a *different* application (also excluded).
    """
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={
            "job_id": ids["job_id"],
            "resume_version_id": ids["resume_version_id"],
        },
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    with SessionLocal() as db:
        _persist_readiness_artifact(
            db,
            application_id=app_id,
            job_id=ids["job_id"],
            user_id=ids["user_id"],
            resume_version_id=ids["resume_version_id"],
            artifact_type="hr_opening_message",
            content='{"hook":"h","message":"m"}',
        )
        _persist_readiness_artifact(
            db,
            application_id=app_id,
            job_id=ids["job_id"],
            user_id=ids["user_id"],
            resume_version_id=ids["resume_version_id"],
            artifact_type="skill_gap_plan",
            content='{"critical_gaps":[]}',
        )
        # A jd_analysis artifact for the same job; must be excluded.
        _persist_readiness_artifact(
            db,
            application_id=app_id,
            job_id=ids["job_id"],
            user_id=ids["user_id"],
            resume_version_id=ids["resume_version_id"],
            artifact_type="jd_analysis",
            content='{"summary":"s"}',
        )
        # A readiness artifact bound to a different application id; excluded.
        _persist_readiness_artifact(
            db,
            application_id="other_application",
            job_id=ids["job_id"],
            user_id=ids["user_id"],
            resume_version_id=ids["resume_version_id"],
            artifact_type="interview_prep",
            content='{"likely_questions":[]}',
        )
        db.commit()

    resp = client.get(
        f"/api/v1/applications/{app_id}/artifacts",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["items"]
    # Only the two readiness artifacts bound to this application.
    assert len(body) == 2
    types = {row["artifact_type"] for row in body}
    assert types == {"hr_opening_message", "skill_gap_plan"}
    # Each row carries the validated content + source_ids provenance.
    row_by_type = {row["artifact_type"]: row for row in body}
    assert row_by_type["hr_opening_message"]["content"] == '{"hook":"h","message":"m"}'
    assert row_by_type["skill_gap_plan"]["content"] == '{"critical_gaps":[]}'
    for row in body:
        assert row["source_ids"]["application_id"] == app_id


def test_list_application_artifacts_empty(client: TestClient) -> None:
    """An application with no generated artifacts returns an empty list."""
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.get(
        f"/api/v1/applications/{app_id}/artifacts",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


def test_list_application_artifacts_cross_user_returns_404(
    client: TestClient,
) -> None:
    """Cross-user access returns 404 (not 403), consistent with other endpoints."""
    ids = _seed()
    app_id = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"]},
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.get(
        f"/api/v1/applications/{app_id}/artifacts",
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404
