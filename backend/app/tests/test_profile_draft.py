"""Tests for the resume → profile draft apply flow.

Covers:
- Preview mode (confirm=false) returns a diff without writing.
- Confirm mode (confirm=true) writes allowed changes.
- Overwrite guard: non-empty current fields are blocked without overwrite=true.
- Overwrite allowed: overwrite=true overwrites non-empty current fields.
- Missing facts: no diffs when the resume has no structured facts.
- Cross-user 404 on resume/version access.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import Resume, ResumeVersion, UserProfile
from app.db.session import SessionLocal

# ---------------------------------------------------------------------------
# Helpers: build rows directly against the DB (bypassing the upload pipeline)
# ---------------------------------------------------------------------------


def _make_resume(
    db: Session, user_id: str, raw_text: str, filename: str = "r.txt"
) -> tuple[Resume, ResumeVersion]:
    _ensure_user(db, user_id)
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


def _make_resume_with_facts(
    db: Session,
    user_id: str,
    raw_text: str,
    facts: dict[str, Any],
    filename: str = "r.txt",
) -> tuple[Resume, ResumeVersion]:
    _ensure_user(db, user_id)
    resume = Resume(user_id=user_id, filename=filename)
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        version_no=1,
        raw_text=raw_text,
        parsed_facts={
            "_parser": "text",
            "_parser_status": "parsed",
            "facts": facts,
        },
    )
    db.add(version)
    db.flush()
    return resume, version


def _ensure_user(db: Session, user_id: str) -> None:
    """Ensure a ``UserProfile`` row exists for ``user_id`` (FK requirement)."""
    existing = db.get(UserProfile, user_id)
    if existing is None:
        db.add(UserProfile(id=user_id, display_name="测试用户"))
        db.flush()


_DEFAULT_FACTS: dict[str, Any] = {
    "contact": {"name": "张三", "email": "zs@example.com"},
    "target_direction": "backend",
    "locations": ["北京", "上海"],
    "strengths": ["Python", "FastAPI"],
    "highlights": ["高并发经验"],
}


def _seed_user_and_resume(
    client: TestClient,
    user_id: str = "draft_user",
    profile_fields: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Create a user (with optional profile data) + a resume version with facts.

    Returns ``(resume_id, version_id)``.
    """
    # Ensure the user exists via the API.
    client.get("/api/v1/users/me", headers={"X-User-Id": user_id})

    if profile_fields:
        client.patch(
            "/api/v1/users/me",
            json=profile_fields,
            headers={"X-User-Id": user_id},
        )

    use_facts = facts if facts is not None else _DEFAULT_FACTS

    with SessionLocal() as db:
        resume, version = _make_resume_with_facts(db, user_id, "raw text", use_facts)
        db.commit()
        return resume.id, version.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_preview_returns_diff_without_writing(client: TestClient) -> None:
    resume_id, version_id = _seed_user_and_resume(client)

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": False, "overwrite": False},
        headers={"X-User-Id": "draft_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert body["confirm"] is False
    assert len(body["diffs"]) > 0

    # No write happened — the profile should still be the default.
    got = client.get("/api/v1/users/me", headers={"X-User-Id": "draft_user"})
    assert got.json()["display_name"] == "演示用户"
    assert got.json()["career_direction"] is None


def test_confirm_writes_allowed_changes(client: TestClient) -> None:
    resume_id, version_id = _seed_user_and_resume(client)

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": True, "overwrite": False},
        headers={"X-User-Id": "draft_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["updated_profile"] is not None

    # display_name is "演示用户" (non-empty default) → blocked without overwrite.
    # career_direction / base_location / preferred_locations / strengths are
    # empty → written.
    diff_map = {d["field"]: d for d in body["diffs"]}
    assert diff_map["display_name"]["will_change"] is False
    assert diff_map["career_direction"]["will_change"] is True
    assert diff_map["base_location"]["will_change"] is True
    assert diff_map["preferred_locations"]["will_change"] is True
    assert diff_map["strengths"]["will_change"] is True

    # The profile should now reflect the resume facts for unblocked fields.
    got = client.get("/api/v1/users/me", headers={"X-User-Id": "draft_user"})
    profile = got.json()
    assert profile["display_name"] == "演示用户"  # blocked, unchanged
    assert profile["career_direction"] == "backend"
    assert profile["base_location"] == "北京"  # first item of locations
    assert profile["preferred_locations"] == ["北京", "上海"]
    assert "Python" in profile["strengths"]
    assert "FastAPI" in profile["strengths"]
    assert "高并发经验" in profile["strengths"]


def test_confirm_with_overwrite_writes_display_name(client: TestClient) -> None:
    resume_id, version_id = _seed_user_and_resume(client)

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": True, "overwrite": True},
        headers={"X-User-Id": "draft_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True

    # With overwrite, display_name should be updated from "演示用户" to "张三".
    got = client.get("/api/v1/users/me", headers={"X-User-Id": "draft_user"})
    profile = got.json()
    assert profile["display_name"] == "张三"
    assert profile["career_direction"] == "backend"
    assert profile["base_location"] == "北京"


def test_overwrite_guard_blocks_non_empty_fields(client: TestClient) -> None:
    # Seed a user with existing display_name, career_direction, and base_location.
    resume_id, version_id = _seed_user_and_resume(
        client,
        profile_fields={
            "display_name": "已有名字",
            "career_direction": "frontend",
            "base_location": "深圳",
        },
    )

    # Confirm without overwrite — existing non-empty fields must be blocked.
    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": True, "overwrite": False},
        headers={"X-User-Id": "draft_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # display_name, career_direction, and base_location are blocked (non-empty).
    diff_map = {d["field"]: d for d in body["diffs"]}
    assert diff_map["display_name"]["will_change"] is False
    assert diff_map["display_name"]["blocked_reason"] is not None
    assert diff_map["career_direction"]["will_change"] is False
    assert diff_map["career_direction"]["blocked_reason"] is not None
    assert diff_map["base_location"]["will_change"] is False
    assert diff_map["base_location"]["blocked_reason"] is not None
    # preferred_locations and strengths are empty → should still be writable.
    assert diff_map["preferred_locations"]["will_change"] is True
    assert diff_map["strengths"]["will_change"] is True

    # Profile should keep the existing values for blocked fields.
    got = client.get("/api/v1/users/me", headers={"X-User-Id": "draft_user"})
    profile = got.json()
    assert profile["display_name"] == "已有名字"
    assert profile["career_direction"] == "frontend"
    assert profile["base_location"] == "深圳"
    # But the unblocked fields should have been written.
    assert profile["preferred_locations"] == ["北京", "上海"]


def test_overwrite_true_overwrites_non_empty_fields(client: TestClient) -> None:
    resume_id, version_id = _seed_user_and_resume(
        client,
        profile_fields={
            "display_name": "已有名字",
            "career_direction": "frontend",
            "base_location": "深圳",
        },
    )

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": True, "overwrite": True},
        headers={"X-User-Id": "draft_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    diff_map = {d["field"]: d for d in body["diffs"]}
    assert diff_map["display_name"]["will_change"] is True
    assert diff_map["display_name"]["blocked_reason"] is None
    assert diff_map["base_location"]["will_change"] is True
    assert diff_map["base_location"]["blocked_reason"] is None

    got = client.get("/api/v1/users/me", headers={"X-User-Id": "draft_user"})
    profile = got.json()
    assert profile["display_name"] == "张三"
    assert profile["career_direction"] == "backend"
    assert profile["base_location"] == "北京"


def test_missing_facts_returns_empty_diffs(client: TestClient) -> None:
    # A resume version with no structured facts.
    with SessionLocal() as db:
        resume, version = _make_resume(db, "nofacts_user", "raw text")
        db.commit()
        resume_id, version_id = resume.id, version.id

    client.get("/api/v1/users/me", headers={"X-User-Id": "nofacts_user"})

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": True, "overwrite": False},
        headers={"X-User-Id": "nofacts_user"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert body["diffs"] == []


def test_cross_user_returns_404(client: TestClient) -> None:
    resume_id, version_id = _seed_user_and_resume(client, user_id="owner_user")

    # A different user tries to access the resume.
    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/apply-profile-draft",
        json={"confirm": False, "overwrite": False},
        headers={"X-User-Id": "intruder_user"},
    )
    assert resp.status_code == 404


def test_cross_user_version_404(client: TestClient) -> None:
    # Owner has the resume, but the version_id doesn't belong to this resume.
    resume_id, _ = _seed_user_and_resume(client, user_id="owner2_user")
    # Create a different resume/version for another user.
    with SessionLocal() as db:
        _, other_version = _make_resume_with_facts(
            db, "other_user", "raw", {"contact": {"name": "李四"}}
        )
        db.commit()
        other_version_id = other_version.id

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{other_version_id}/apply-profile-draft",
        json={"confirm": False, "overwrite": False},
        headers={"X-User-Id": "owner2_user"},
    )
    assert resp.status_code == 404
