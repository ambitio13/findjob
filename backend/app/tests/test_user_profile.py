"""Tests for the current-user profile and job-search preferences workflow.

Covers design §10 tests 1-7 plus jobs ownership tests (D2). All endpoints are
reached through the real FastAPI app via ``TestClient``; the current user is
resolved from the ``X-User-Id`` header (defaulting to ``demo_user`` when
absent). Tests run against the rolled-back test database, so each test starts
from a clean state.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Profile: design §10 tests 1-7
# ---------------------------------------------------------------------------


def test_get_me_without_header_returns_demo_user(client: TestClient) -> None:
    # Test 1: GET /me without X-User-Id -> 200, demo_user profile auto-created.
    resp = client.get("/api/v1/users/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "demo_user"
    assert body["display_name"] == "演示用户"


def test_get_me_with_custom_header(client: TestClient) -> None:
    # Test 2: GET /me with X-User-Id header -> 200 with that user id.
    resp = client.get("/api/v1/users/me", headers={"X-User-Id": "custom_user"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "custom_user"
    assert body["display_name"] == "演示用户"


def test_patch_me_persists_update(client: TestClient) -> None:
    # Test 3: PATCH /me updates display_name + salary and persists.
    resp = client.patch(
        "/api/v1/users/me",
        json={"display_name": "李雷", "salary_min": 15000, "salary_max": 30000},
        headers={"X-User-Id": "patch_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "李雷"
    assert body["salary_min"] == 15000
    assert body["salary_max"] == 30000

    # Re-fetch to confirm persistence.
    again = client.get("/api/v1/users/me", headers={"X-User-Id": "patch_user"})
    assert again.status_code == 200
    again_body = again.json()
    assert again_body["display_name"] == "李雷"
    assert again_body["salary_min"] == 15000
    assert again_body["salary_max"] == 30000


def test_patch_me_rejects_invalid_salary_range(client: TestClient) -> None:
    # Test 4: PATCH /me with salary_min > salary_max -> 422.
    resp = client.patch(
        "/api/v1/users/me",
        json={"salary_min": 200000, "salary_max": 100000},
        headers={"X-User-Id": "bad_salary_user"},
    )
    assert resp.status_code == 422


def test_get_me_is_idempotent_single_row(client: TestClient) -> None:
    # Test 5: Two GET /me calls produce the same id and a single row.
    first = client.get("/api/v1/users/me", headers={"X-User-Id": "idem_user"})
    assert first.status_code == 200
    first_id = first.json()["id"]

    second = client.get("/api/v1/users/me", headers={"X-User-Id": "idem_user"})
    assert second.status_code == 200
    assert second.json()["id"] == first_id

    # Query the DB directly to confirm only one row exists for this user.
    from sqlalchemy import func, select

    from app.db.models.models import UserProfile
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        count = db.execute(
            select(func.count()).select_from(UserProfile).where(UserProfile.id == "idem_user")
        ).scalar_one()
        assert count == 1


def test_repeated_get_me_does_not_bump_updated_at(client: TestClient) -> None:
    # Test 6: Repeated GET /me must not change updated_at (read-only upsert
    # uses ON CONFLICT DO NOTHING, which does not touch updated_at).
    first = client.get("/api/v1/users/me", headers={"X-User-Id": "no_bump_user"})
    assert first.status_code == 200
    first_updated_at = first.json()["updated_at"]

    second = client.get("/api/v1/users/me", headers={"X-User-Id": "no_bump_user"})
    assert second.status_code == 200
    assert second.json()["updated_at"] == first_updated_at


def test_json_fields_round_trip(client: TestClient) -> None:
    # Test 7: JSON fields (preferred_locations, strengths) round-trip through
    # PATCH -> GET. Named constraint fields are tested separately below.
    payload: dict[str, Any] = {
        "preferred_locations": ["北京", "上海", "杭州"],
        "strengths": ["Python", "系统设计", "团队协作"],
    }
    patch = client.patch(
        "/api/v1/users/me",
        json=payload,
        headers={"X-User-Id": "json_user"},
    )
    assert patch.status_code == 200

    got = client.get("/api/v1/users/me", headers={"X-User-Id": "json_user"})
    assert got.status_code == 200
    body = got.json()
    assert body["preferred_locations"] == ["北京", "上海", "杭州"]
    assert body["strengths"] == ["Python", "系统设计", "团队协作"]


def test_named_constraints_round_trip(client: TestClient) -> None:
    # Named constraint fields are sent as typed sub-fields, stored in the
    # ``constraints`` JSON column, and read back as ``named_constraints``.
    payload: dict[str, Any] = {
        "named_constraints": {
            "deal_breakers": "不接受996",
            "preferred_company_types": "外企",
            "work_mode_preference": "远程优先",
        },
    }
    patch = client.patch(
        "/api/v1/users/me",
        json=payload,
        headers={"X-User-Id": "named_user"},
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["named_constraints"]["deal_breakers"] == "不接受996"
    assert body["named_constraints"]["preferred_company_types"] == "外企"
    assert body["named_constraints"]["work_mode_preference"] == "远程优先"

    # Re-fetch to confirm persistence.
    got = client.get("/api/v1/users/me", headers={"X-User-Id": "named_user"})
    assert got.status_code == 200
    got_body = got.json()
    assert got_body["named_constraints"]["deal_breakers"] == "不接受996"
    assert got_body["named_constraints"]["preferred_company_types"] == "外企"
    assert got_body["named_constraints"]["work_mode_preference"] == "远程优先"


def test_named_constraints_clear_single_field(client: TestClient) -> None:
    # Sending a named constraint field as null clears just that key, leaving
    # other named fields intact (PATCH null-clears semantics).
    client.patch(
        "/api/v1/users/me",
        json={
            "named_constraints": {
                "deal_breakers": "不接受996",
                "career_goals": "技术专家",
            },
        },
        headers={"X-User-Id": "clear_user"},
    )
    resp = client.patch(
        "/api/v1/users/me",
        json={"named_constraints": {"deal_breakers": None}},
        headers={"X-User-Id": "clear_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["named_constraints"]["deal_breakers"] is None
    assert body["named_constraints"]["career_goals"] == "技术专家"


def test_legacy_constraints_preserved(client: TestClient) -> None:
    # Legacy unknown keys inside ``constraints`` (not in the v1 named set)
    # must survive a named-constraints update and surface as
    # ``legacy_constraints`` (read-only).
    from sqlalchemy import select

    from app.db.models.models import UserProfile
    from app.db.session import SessionLocal

    # Seed a profile with a legacy constraints dict directly in the DB.
    with SessionLocal() as db:
        user = UserProfile(
            id="legacy_user",
            display_name="老用户",
            constraints={"remote": True, "equity": True},
        )
        db.add(user)
        db.commit()

    # The GET response should show legacy_constraints and empty named.
    got = client.get("/api/v1/users/me", headers={"X-User-Id": "legacy_user"})
    assert got.status_code == 200
    body = got.json()
    assert body["legacy_constraints"] == {"remote": True, "equity": True}
    assert body["named_constraints"]["deal_breakers"] is None

    # Update a named field — legacy keys must survive.
    patch = client.patch(
        "/api/v1/users/me",
        json={"named_constraints": {"deal_breakers": "不接受996"}},
        headers={"X-User-Id": "legacy_user"},
    )
    assert patch.status_code == 200
    pbody = patch.json()
    assert pbody["named_constraints"]["deal_breakers"] == "不接受996"
    assert pbody["legacy_constraints"] == {"remote": True, "equity": True}

    # Confirm the DB still has the legacy keys.
    with SessionLocal() as db:
        row = db.execute(select(UserProfile).where(UserProfile.id == "legacy_user")).scalar_one()
        assert row.constraints["remote"] is True
        assert row.constraints["equity"] is True
        assert row.constraints["deal_breakers"] == "不接受996"


# ---------------------------------------------------------------------------
# Jobs ownership: D2
# ---------------------------------------------------------------------------

_JOB_PAYLOAD = {
    "company": "Acme",
    "title": "Backend Engineer",
    "jd_raw": "Build scalable APIs with FastAPI and PostgreSQL.",
}


def test_post_job_without_header_binds_demo_user(client: TestClient) -> None:
    # D2.1: POST /jobs without header binds the job to demo_user.
    create = client.post("/api/v1/jobs", json=_JOB_PAYLOAD)
    assert create.status_code == 201
    job = create.json()
    job_id = job["id"]

    # demo_user should see it in their list.
    listing = client.get("/api/v1/jobs")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(j["id"] == job_id for j in items)


def test_post_job_with_header_binds_named_user(client: TestClient) -> None:
    # D2.2: POST /jobs with X-User-Id binds the job to that user.
    create = client.post(
        "/api/v1/jobs",
        json=_JOB_PAYLOAD,
        headers={"X-User-Id": "job_owner"},
    )
    assert create.status_code == 201
    job_id = create.json()["id"]

    # job_owner sees it.
    own = client.get("/api/v1/jobs", headers={"X-User-Id": "job_owner"})
    assert own.status_code == 200
    assert any(j["id"] == job_id for j in own.json()["items"])

    # demo_user (different user) does NOT see it.
    other = client.get("/api/v1/jobs")
    assert other.status_code == 200
    assert not any(j["id"] == job_id for j in other.json()["items"])


def test_list_jobs_only_returns_current_user_jobs(client: TestClient) -> None:
    # D2.3: GET /jobs only lists the current user's jobs.
    a = client.post(
        "/api/v1/jobs",
        json={**_JOB_PAYLOAD, "company": "A"},
        headers={"X-User-Id": "user_a"},
    )
    b = client.post(
        "/api/v1/jobs",
        json={**_JOB_PAYLOAD, "company": "B"},
        headers={"X-User-Id": "user_b"},
    )
    assert a.status_code == 201
    assert b.status_code == 201
    a_id, b_id = a.json()["id"], b.json()["id"]

    a_list = client.get("/api/v1/jobs", headers={"X-User-Id": "user_a"})
    assert a_list.status_code == 200
    a_ids = {j["id"] for j in a_list.json()["items"]}
    assert a_id in a_ids
    assert b_id not in a_ids


def test_get_job_detail_404_for_other_user(client: TestClient) -> None:
    # D2.4: GET /jobs/{job_id} returns 404 for another user's job.
    create = client.post(
        "/api/v1/jobs",
        json=_JOB_PAYLOAD,
        headers={"X-User-Id": "owner_only"},
    )
    assert create.status_code == 201
    job_id = create.json()["id"]

    # Owner can see it.
    owner = client.get(f"/api/v1/jobs/{job_id}", headers={"X-User-Id": "owner_only"})
    assert owner.status_code == 200

    # Another user gets 404 (not 403).
    intruder = client.get(f"/api/v1/jobs/{job_id}", headers={"X-User-Id": "intruder"})
    assert intruder.status_code == 404

    # demo_user (no header) also gets 404.
    default = client.get(f"/api/v1/jobs/{job_id}")
    assert default.status_code == 404
