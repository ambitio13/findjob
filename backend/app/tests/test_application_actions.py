"""API integration tests for the external-action approval boundary.

Covers the acceptance criteria in the PRD:

- User can preview a planned action payload.
- User can approve that exact payload.
- Approval stores user id, timestamp, payload hash, source ids, action type.
- Payload/source changes mark approval stale (guarded via
  ``assert_action_approved``).
- Unapproved or stale actions are blocked by the guard.
- Revoking approval blocks execution.
- No external action execution is implemented.

The test DB is a real PostgreSQL instance (see ``conftest.py``). Each test gets
a truncated schema.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import JobPosting, Resume, ResumeVersion, UserProfile
from app.db.session import SessionLocal
from app.schemas.application_action import (
    ApplicationActionOut,
    ApplicationActionPreview,
    ApplicationActionSourceSnapshot,
    ApprovalBlockedError,
    ExternalActionStatus,
    ExternalActionType,
)
from app.services.approval_boundary import (
    assert_action_approved,
    compute_payload_hash,
)

# ---------------------------------------------------------------------------
# DB helpers (mirrors test_applications_api.py shape)
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
    user_id: str = "approval_user",
    *,
    other_user_id: str = "approval_other",
) -> dict[str, str]:
    """Seed two users and return their IDs + job/resume IDs."""
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


def _create_application(client: TestClient, ids: dict[str, str]) -> str:
    """Create an application record and return its id."""
    resp = client.post(
        "/api/v1/applications",
        json={"job_id": ids["job_id"], "resume_version_id": ids["resume_version_id"]},
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _source_snapshot(ids: dict[str, str], source_hash: str = "sha256:abc") -> dict:
    return {
        "job_id": ids["job_id"],
        "resume_version_id": ids["resume_version_id"],
        "artifact_ids": [],
        "source_hash": source_hash,
    }


def _preview_payload(
    ids: dict[str, str],
    *,
    action_type: str = "platform_submit",
    outgoing_text: str = "您好，我对这个职位很感兴趣。",
    source_hash: str = "sha256:abc",
) -> dict:
    return {
        "action_type": action_type,
        "target_platform": "boss",
        "target_resource": "https://example.com/job/123",
        "selected_artifact_ids": [],
        "outgoing_text": outgoing_text,
        "resume_file_reference": ids["resume_version_id"],
        "source_snapshot": _source_snapshot(ids, source_hash),
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_creates_approval_required_action(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)

    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["application_id"] == app_id
    assert body["user_id"] == ids["user_id"]
    assert body["action_type"] == "platform_submit"
    assert body["status"] == "approval_required"
    assert body["payload_hash"].startswith("sha256:")
    assert body["approval"] is None
    assert body["stale_reason"] is None
    # The preview must echo the submitted payload.
    assert body["payload_preview"]["outgoing_text"] == "您好，我对这个职位很感兴趣。"
    assert body["payload_preview"]["target_platform"] == "boss"


def test_preview_appends_timeline_event(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)

    client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    )

    detail = client.get(
        f"/api/v1/applications/{app_id}", headers=_headers(ids["user_id"])
    ).json()
    # created + action_previewed
    types = [e["type"] for e in detail["timeline"]]
    assert "action_previewed" in types


def test_preview_cross_user_application_returns_404(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)

    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "application not found"


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


def test_approve_binds_exact_payload_hash(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)

    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    approval = body["approval"]
    assert approval["approved_by"] == ids["user_id"]
    assert "approved_at" in approval
    # The approved hash must equal the action's payload hash.
    assert approval["approved_payload_hash"] == body["payload_hash"]


def test_approve_appends_timeline_event(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["user_id"]),
    )

    detail = client.get(
        f"/api/v1/applications/{app_id}", headers=_headers(ids["user_id"])
    ).json()
    types = [e["type"] for e in detail["timeline"]]
    assert "action_approved" in types


def test_approve_unknown_action_returns_404(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/act_missing/approve",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 404


def test_approve_cross_user_action_returns_404(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404


def test_approve_revoked_action_returns_409(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]
    # Approve then revoke.
    client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["user_id"]),
    )
    client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/revoke",
        headers=_headers(ids["user_id"]),
    )
    # Re-approving a revoked action without re-preview is not allowed.
    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_revoke_clears_approval(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]
    client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["user_id"]),
    )

    resp = client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/revoke",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "revoked"
    assert body["approval"] is None


def test_revoke_appends_timeline_event(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]
    client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/approve",
        headers=_headers(ids["user_id"]),
    )
    client.post(
        f"/api/v1/applications/{app_id}/actions/{action_id}/revoke",
        headers=_headers(ids["user_id"]),
    )

    detail = client.get(
        f"/api/v1/applications/{app_id}", headers=_headers(ids["user_id"])
    ).json()
    types = [e["type"] for e in detail["timeline"]]
    assert "action_revoked" in types


# ---------------------------------------------------------------------------
# Read + list
# ---------------------------------------------------------------------------


def test_get_action_detail(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    resp = client.get(
        f"/api/v1/applications/{app_id}/actions/{action_id}",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == action_id


def test_list_actions_for_application(client: TestClient) -> None:
    ids = _seed()
    app_id = _create_application(client, ids)
    for at in ("platform_submit", "hr_message"):
        client.post(
            f"/api/v1/applications/{app_id}/actions/preview",
            json=_preview_payload(ids, action_type=at),
            headers=_headers(ids["user_id"]),
        )

    resp = client.get(
        f"/api/v1/applications/{app_id}/actions",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    types = {i["action_type"] for i in items}
    assert types == {"platform_submit", "hr_message"}


def test_list_actions_cross_user_returns_empty(client: TestClient) -> None:
    """The other user owns their own application, so this app is 404 to them."""
    ids = _seed()
    app_id = _create_application(client, ids)
    client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    )

    resp = client.get(
        f"/api/v1/applications/{app_id}/actions",
        headers=_headers(ids["other_user_id"]),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Execution guard (assert_action_approved) — unit-level
# ---------------------------------------------------------------------------


def _make_action_out(
    *,
    status: ExternalActionStatus = ExternalActionStatus.approved,
    payload_hash: str = "sha256:payload",
    source_hash: str = "sha256:source",
    approved_payload_hash: str | None = "sha256:payload",
    action_id: str = "act_test",
) -> ApplicationActionOut:
    preview = ApplicationActionPreview(
        action_type=ExternalActionType.platform_submit,
        target_platform="boss",
        target_resource=None,
        selected_artifact_ids=[],
        outgoing_text="hi",
        resume_file_reference=None,
    )
    snapshot = ApplicationActionSourceSnapshot(
        job_id="job_1",
        resume_version_id=None,
        artifact_ids=[],
        source_hash=source_hash,
    )
    approval = None
    if approved_payload_hash is not None:
        from app.schemas.application_action import ApprovalRecord

        approval = ApprovalRecord(
            approved_by="u1",
            approved_at=datetime.now(UTC),
            approved_payload_hash=approved_payload_hash,
        )
    return ApplicationActionOut(
        id=action_id,
        application_id="app_1",
        user_id="u1",
        action_type=ExternalActionType.platform_submit,
        status=status,
        payload_preview=preview,
        payload_hash=payload_hash,
        source_snapshot=snapshot,
        approval=approval,
        stale_reason=None,
    )


def test_guard_allows_approved_matching_payload_and_source() -> None:
    action = _make_action_out()
    # Must not raise.
    assert_action_approved(
        action,
        current_payload_hash="sha256:payload",
        current_source_hash="sha256:source",
    )


def test_guard_blocks_missing_approval() -> None:
    action = _make_action_out(status=ExternalActionStatus.approval_required)
    with pytest.raises(ApprovalBlockedError) as exc:
        assert_action_approved(action)
    assert exc.value.reason == "not_approved"


def test_guard_blocks_revoked() -> None:
    action = _make_action_out(status=ExternalActionStatus.revoked)
    with pytest.raises(ApprovalBlockedError) as exc:
        assert_action_approved(action)
    assert exc.value.reason == "revoked"


def test_guard_blocks_stale_status() -> None:
    action = _make_action_out(status=ExternalActionStatus.stale)
    with pytest.raises(ApprovalBlockedError) as exc:
        assert_action_approved(action)
    assert exc.value.reason == "stale"


def test_guard_blocks_payload_hash_mismatch() -> None:
    action = _make_action_out(approved_payload_hash="sha256:original")
    with pytest.raises(ApprovalBlockedError) as exc:
        assert_action_approved(action, current_payload_hash="sha256:changed")
    assert exc.value.reason == "payload_hash_mismatch"
    assert exc.value.current_payload_hash == "sha256:changed"
    assert exc.value.approved_payload_hash == "sha256:original"


def test_guard_blocks_source_stale() -> None:
    action = _make_action_out(source_hash="sha256:original")
    with pytest.raises(ApprovalBlockedError) as exc:
        assert_action_approved(action, current_source_hash="sha256:changed")
    assert exc.value.reason == "source_stale"


def test_guard_blocks_approved_status_without_approval_record() -> None:
    """Defensive: status says approved but approval record is missing."""
    action = _make_action_out(approved_payload_hash=None)
    with pytest.raises(ApprovalBlockedError) as exc:
        assert_action_approved(action)
    assert exc.value.reason == "missing_approval"


# ---------------------------------------------------------------------------
# Payload hash stability
# ---------------------------------------------------------------------------


def test_payload_hash_stable_for_identical_preview() -> None:
    preview_a = ApplicationActionPreview(
        action_type=ExternalActionType.hr_message,
        target_platform="lagou",
        target_resource="conv_1",
        selected_artifact_ids=["art_a", "art_b"],
        outgoing_text="您好",
        resume_file_reference=None,
    )
    preview_b = copy.deepcopy(preview_a)
    # Artifact id order should not matter (sorted in normalize).
    preview_b.selected_artifact_ids = ["art_b", "art_a"]
    assert compute_payload_hash(preview_a) == compute_payload_hash(preview_b)


def test_payload_hash_changes_when_outgoing_text_changes() -> None:
    base = ApplicationActionPreview(
        action_type=ExternalActionType.hr_message,
        target_platform=None,
        target_resource=None,
        selected_artifact_ids=[],
        outgoing_text="您好",
        resume_file_reference=None,
    )
    changed = base.model_copy(update={"outgoing_text": "你好"})
    assert compute_payload_hash(base) != compute_payload_hash(changed)


def test_payload_hash_changes_when_action_type_changes() -> None:
    base = ApplicationActionPreview(
        action_type=ExternalActionType.platform_submit,
        target_platform=None,
        target_resource=None,
        selected_artifact_ids=[],
        outgoing_text=None,
        resume_file_reference=None,
    )
    changed = base.model_copy(
        update={"action_type": ExternalActionType.follow_up_message}
    )
    assert compute_payload_hash(base) != compute_payload_hash(changed)


# ---------------------------------------------------------------------------
# No external execution path
# ---------------------------------------------------------------------------


def test_no_external_execution_endpoint_exists(client: TestClient) -> None:
    """There must be no /execute or /submit endpoint that performs the action.

    The approval boundary only prepares actions; execution is intentionally
    absent so the first automated agent is forced to call
    ``assert_action_approved`` from its own tool layer.
    """
    ids = _seed()
    app_id = _create_application(client, ids)
    action_id = client.post(
        f"/api/v1/applications/{app_id}/actions/preview",
        json=_preview_payload(ids),
        headers=_headers(ids["user_id"]),
    ).json()["id"]

    # Neither execute nor submit should be routed.
    for verb, path in (
        ("post", f"/api/v1/applications/{app_id}/actions/{action_id}/execute"),
        ("post", f"/api/v1/applications/{app_id}/actions/{action_id}/submit"),
    ):
        resp = getattr(client, verb)(
            path, headers=_headers(ids["user_id"])
        )
        assert resp.status_code == 404, f"{verb.upper()} {path} should not exist"
