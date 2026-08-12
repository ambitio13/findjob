"""Service integration tests for the BOSS immediate-communicate workflow.

Covers the prepare + execute-gate phases:

- Happy path: seed user + resume + job + match artifact (communicate decision)
  → prepare returns an action with ``boss_immediate_communicate`` type,
  ``approval_required`` status, ``sha256:`` payload hash, and idempotency key
  prefix ``{application_id}:boss_immediate_communicate:``.
- Cross-user job → 404.
- Missing resume version → 404.
- Match artifact not found → 404.
- Wrong artifact type → 404.
- Skip decision → 422.
- needs_review decision → 422.
- Opening message None → 422.
- Re-prepare reuses the existing non-terminal action (no duplicates).
- ``source_snapshot`` contains ``job_url_hash`` and ``decision_trace``.
- ``payload_hash`` changes when opening message changes.
- Timeline event ``boss_communicate_previewed`` appended.

Execute-gate tests:

- Unapproved execute → ``BossCommunicateBlockedError`` (reason=``not_approved``).
- Approved execute → guards pass, ``external_started_at`` set,
  ``boss_communicate_started`` timeline appended.
- Idempotency replay → returns existing action, ``boss_communicate_blocked``
  timeline with ``reason=idempotency_replay``.
- Payload hash mismatch → ``BossCommunicateBlockedError`` (reason=``payload_hash_mismatch``).
- Source stale → ``BossCommunicateBlockedError`` (reason=``source_stale``).
- Cross-user action → 404.
- Wrong action type → 404.

All tests use the ``client`` fixture so the DB is truncated per test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models.models import (
    ApplicationAction,
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.models_gateway.fake import FakeModelGateway
from app.services.boss_communicate_service import (
    BossCommunicateBlockedError,
    prepare_communicate_action,
    run_boss_communicate_execute,
)
from app.services.boss_match_service import run_boss_match_decision

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "comm_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="Communicate 用户")
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
    external_id: str | None = None,
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
    user_id: str = "comm_user",
    *,
    other_user_id: str = "comm_other",
    external_id: str | None = "sha256:page_url_hash_abc",
) -> dict[str, str]:
    """Seed a user + resume + version + job, returning their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id)
        job = _make_job(db, user_id, external_id=external_id)

        # Seed a second user for cross-user tests.
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


def _get_user(user_id: str) -> UserProfile:
    with SessionLocal() as db:
        user = db.get(UserProfile, user_id)
        assert user is not None
        return user


def _get_other_user(user_id: str) -> UserProfile:
    return _get_user(user_id)


async def _run_match(
    ids: dict[str, str], *, scenario: str = "communicate"
) -> str:
    """Run the boss match decision and return the persisted artifact ID.

    The default fake gateway returns the ``communicate`` scenario. Patch
    ``_BOSS_MATCH_FAKE_OUTPUTS`` to use a different scenario.
    """
    user = _get_user(ids["user_id"])
    with SessionLocal() as db:
        _, artifact, _, _, _ = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )
        return artifact.id


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
    artifact_type: str = "boss_match_decision",
    job_id: str | None = None,
) -> str:
    """Seed a ``boss_match_decision`` artifact directly (no model call).

    Returns the artifact ID. This gives precise control over the artifact
    content for error-path tests (skip, needs_review, None opening message,
    wrong type, etc.).
    """
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
            artifact_type=artifact_type,
            content=content,
            user_id=ids["user_id"],
            job_id=job_id or ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            source_ids={
                "workflow_type": "boss_match_decision",
                "job_id": job_id or ids["job_id"],
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


def _approve_action(action_id: str, application_id: str, user_id: str) -> None:
    """Approve a communicate action via the approval service (sets status=approved)."""
    from app.services.approval_action_service import approve_action

    with SessionLocal() as db:
        user = db.get(UserProfile, user_id)
        assert user is not None
        approve_action(db, user, application_id, action_id)


# ---------------------------------------------------------------------------
# Prepare — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_happy_path_creates_approval_required_action(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )

    assert action.action_type == "boss_immediate_communicate"
    assert action.status == "approval_required"
    assert action.payload_hash.startswith("sha256:")
    assert action.approval is None
    assert action.stale_reason is None
    # Idempotency key shape: {application_id}:boss_immediate_communicate:{payload_hash}
    assert action.external_idempotency_key is not None
    prefix = f"{action.application_id}:boss_immediate_communicate:"
    assert action.external_idempotency_key.startswith(prefix)
    assert action.external_idempotency_key.endswith(action.payload_hash)
    # The preview must carry the opening message and correct targets.
    preview = action.payload_preview
    assert preview["action_type"] == "boss_immediate_communicate"
    assert preview["target_platform"] == "boss"
    assert preview["target_resource"] == ids["job_id"]
    assert preview["outgoing_text"] is not None
    assert len(preview["outgoing_text"]) >= 10


@pytest.mark.asyncio
async def test_prepare_appends_boss_communicate_previewed_timeline(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        # Read the application timeline.
        from app.db.models.models import ApplicationRecord

        record = db.get(ApplicationRecord, action.application_id)
        assert record is not None
        types = [e["type"] for e in record.timeline]
        assert "boss_communicate_previewed" in types


@pytest.mark.asyncio
async def test_prepare_source_snapshot_contains_job_url_hash_and_decision_trace(
    client,
) -> None:
    ids = _seed(external_id="sha256:page_url_hash_xyz")
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        snap = action.source_snapshot
        assert snap["job_url_hash"] == "sha256:page_url_hash_xyz"
        assert "decision_trace" in snap
        # decision_trace excludes opening_message.
        trace: dict[str, Any] = snap["decision_trace"]
        assert "opening_message" not in trace
        assert trace["decision"] == "communicate"
        assert "score" in trace
        assert "reasons" in trace


@pytest.mark.asyncio
async def test_prepare_reuses_existing_non_terminal_action(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action1 = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        action_id_1 = action1.id

    # Re-prepare with the same artifact.
    with SessionLocal() as db:
        action2 = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        action_id_2 = action2.id

    assert action_id_1 == action_id_2  # same action refreshed, no duplicate

    # Verify only one boss_immediate_communicate action exists for this application.
    with SessionLocal() as db:
        count = (
            db.query(ApplicationAction)
            .filter(
                ApplicationAction.application_id == action2.application_id,
                ApplicationAction.action_type == "boss_immediate_communicate",
            )
            .count()
        )
        assert count == 1


@pytest.mark.asyncio
async def test_prepare_payload_hash_changes_with_different_opening_message(
    client,
) -> None:
    ids = _seed()
    # Seed two artifacts with different opening messages.
    artifact_a = _seed_artifact_directly(
        ids,
        opening_message="您好，我是一名有5年Python后端开发经验的工程师，对贵司职位感兴趣。",
    )
    artifact_b = _seed_artifact_directly(
        ids,
        opening_message="您好，我有丰富的FastAPI经验，希望能和您进一步交流这个职位。",
    )
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action_a = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_a,
        )
        hash_a = action_a.payload_hash

    with SessionLocal() as db:
        action_b = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_b,
        )
        hash_b = action_b.payload_hash

    assert hash_a != hash_b


# ---------------------------------------------------------------------------
# Prepare — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_cross_user_job_returns_404(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    other_user = _get_other_user(ids["other_user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                other_user,
                ids["job_id"],
                resume_version_id=ids["other_resume_version_id"],
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_prepare_missing_resume_version_returns_404(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id="nonexistent_version",
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_prepare_artifact_not_found_returns_404(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id="nonexistent_artifact",
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_prepare_wrong_artifact_type_returns_404(client) -> None:
    ids = _seed()
    # Seed an artifact with the wrong type.
    artifact_id = _seed_artifact_directly(ids, artifact_type="jd_analysis")
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_prepare_skip_decision_returns_422(client) -> None:
    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="skip",
        score=0.25,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_prepare_needs_review_decision_returns_422(client) -> None:
    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_prepare_none_opening_message_returns_422(client) -> None:
    """Defensive: a communicate decision with a null opening message → 422."""
    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="communicate",
        score=0.82,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Execute — guard chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_unapproved_returns_blocked_not_approved(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    with SessionLocal() as db:
        with pytest.raises(BossCommunicateBlockedError) as exc_info:
            await run_boss_communicate_execute(
                db,
                current_user=user,
                job_id=ids["job_id"],
                application_id=application_id,
                action_id=action_id,
            )
    assert exc_info.value.reason == "not_approved"


@pytest.mark.asyncio
async def test_execute_approved_invokes_adapter_and_persists_result(
    client, monkeypatch
) -> None:
    """Approved execute calls the fake adapter (succeeded) and persists the
    terminal result: ``external_result_status == "submitted"`` (succeeded maps
    to submitted), ``external_completed_at`` set, and
    ``boss_communicate_succeeded`` timeline event appended.
    """
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    # Approve the action.
    _approve_action(action_id, application_id, ids["user_id"])

    # Inject a fake adapter with the communicate_succeeded scenario.
    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_succeeded")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        record, action, _ = await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    assert action.external_started_at is not None
    assert action.external_completed_at is not None
    # succeeded → external_result_status == "submitted"
    assert action.external_result_status == "submitted"
    # The adapter was called exactly once.
    assert len(fake.communicate_calls) == 1
    assert fake.communicate_calls[0].application_id == application_id

    # Verify the timeline has boss_communicate_succeeded.
    from app.db.models.models import ApplicationRecord

    with SessionLocal() as db:
        record_db = db.get(ApplicationRecord, application_id)
        assert record_db is not None
        types = [e["type"] for e in record_db.timeline]
        assert "boss_communicate_started" in types
        assert "boss_communicate_succeeded" in types


@pytest.mark.asyncio
async def test_execute_adapter_returns_duplicate(client, monkeypatch) -> None:
    """When the fake adapter returns ``duplicate``, the service persists
    ``external_result_status == "duplicate"`` and appends the
    ``boss_communicate_duplicate`` timeline event."""
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_duplicate")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        record, action, _ = await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    assert action.external_result_status == "duplicate"
    assert action.external_completed_at is not None

    from app.db.models.models import ApplicationRecord

    with SessionLocal() as db:
        record_db = db.get(ApplicationRecord, application_id)
        assert record_db is not None
        types = [e["type"] for e in record_db.timeline]
        assert "boss_communicate_duplicate" in types


@pytest.mark.asyncio
async def test_execute_adapter_returns_failed(client, monkeypatch) -> None:
    """When the fake adapter returns ``failed`` (immediate_button_missing),
    the service persists ``external_result_status == "failed"`` and a failure
    envelope on the application record."""
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_failed")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        record, action, _ = await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    assert action.external_result_status == "failed"
    assert action.external_completed_at is not None

    from app.db.models.models import ApplicationRecord

    with SessionLocal() as db:
        record_db = db.get(ApplicationRecord, application_id)
        assert record_db is not None
        types = [e["type"] for e in record_db.timeline]
        assert "boss_communicate_failed" in types
        # The failure envelope should carry the code.
        assert record_db.latest_error is not None
        assert record_db.latest_error["code"] == "communication_failure"


@pytest.mark.asyncio
async def test_execute_adapter_returns_unknown(client, monkeypatch) -> None:
    """When the fake adapter returns ``unknown``, the service persists
    ``external_result_status == "unknown"`` (hard stop, no retry) and a
    failure envelope with ``retryable=False``."""
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_unknown")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        record, action, _ = await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    assert action.external_result_status == "unknown"
    assert action.external_completed_at is not None

    from app.db.models.models import ApplicationRecord

    with SessionLocal() as db:
        record_db = db.get(ApplicationRecord, application_id)
        assert record_db is not None
        types = [e["type"] for e in record_db.timeline]
        assert "boss_communicate_unknown" in types
        assert record_db.latest_error is not None
        assert record_db.latest_error["retryable"] is False


@pytest.mark.asyncio
async def test_execute_no_adapter_call_before_approval(client, monkeypatch) -> None:
    """The adapter must NOT be called when the action is unapproved —
    ``BossCommunicateBlockedError`` is raised before reaching the adapter."""
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    # Do NOT approve.

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_succeeded")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        with pytest.raises(BossCommunicateBlockedError) as exc_info:
            await run_boss_communicate_execute(
                db,
                current_user=user,
                job_id=ids["job_id"],
                application_id=application_id,
                action_id=action_id,
            )
    assert exc_info.value.reason == "not_approved"
    # The adapter was never called.
    assert len(fake.communicate_calls) == 0


@pytest.mark.asyncio
async def test_execute_idempotency_replay_returns_existing_action(
    client, monkeypatch
) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    # First execute: guards pass.
    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_succeeded")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    # The first execute produced a terminal result. Simulate the shape that
    # an external idempotency replay would find (the real idempotency check
    # looks at external_result_status on the action row).
    from app.db.repositories import application_action_repo

    with SessionLocal() as db:
        action = application_action_repo.get_for_user_and_application(
            db, action_id=action_id, application_id=application_id, user_id=ids["user_id"]
        )
        assert action is not None
        assert action.external_result_status == "submitted"
        now = datetime.now(UTC)
        started = action.external_started_at or now
        application_action_repo.update(
            db,
            action,
            external_completed_at=now,
            external_result_status="submitted",
            external_result={
                "result_status": "submitted",
                "started_at": started.isoformat(),
                "completed_at": now.isoformat(),
                "result": {"platform": "boss"},
            },
        )
        db.commit()

    # Second execute: idempotency replay should return the existing action
    # without re-running guards or touching the browser.
    with SessionLocal() as db:
        record, action, _ = await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    assert action.external_result_status == "submitted"

    # Verify the timeline has boss_communicate_blocked with reason=idempotency_replay.
    from app.db.models.models import ApplicationRecord

    with SessionLocal() as db:
        record_db = db.get(ApplicationRecord, application_id)
        assert record_db is not None
        blocked_events = [
            e for e in record_db.timeline if e["type"] == "boss_communicate_blocked"
        ]
        assert len(blocked_events) >= 1
        assert blocked_events[-1]["metadata"]["reason"] == "idempotency_replay"


@pytest.mark.asyncio
async def test_execute_payload_hash_mismatch_returns_blocked(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    # Tamper with the stored preview so the recomputed payload hash differs.
    from app.db.repositories import application_action_repo

    with SessionLocal() as db:
        action = application_action_repo.get_for_user_and_application(
            db, action_id=action_id, application_id=application_id, user_id=ids["user_id"]
        )
        assert action is not None
        tampered_preview = dict(action.payload_preview)
        tampered_preview["outgoing_text"] = "TAMPERED: completely different message text"
        # Recompute the payload_hash to match the tampered preview (so the
        # action's stored hash is consistent), but the approval record still
        # has the original hash → the guard detects the mismatch.
        from app.schemas.application_action import ApplicationActionPreview
        from app.services.approval_boundary import compute_payload_hash

        new_preview = ApplicationActionPreview.model_validate(tampered_preview)
        new_hash = compute_payload_hash(new_preview)
        application_action_repo.update(
            db,
            action,
            payload_preview=tampered_preview,
            payload_hash=new_hash,
        )
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(BossCommunicateBlockedError) as exc_info:
            await run_boss_communicate_execute(
                db,
                current_user=user,
                job_id=ids["job_id"],
                application_id=application_id,
                action_id=action_id,
            )
    assert exc_info.value.reason == "payload_hash_mismatch"


@pytest.mark.asyncio
async def test_execute_source_stale_returns_blocked(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    # Change the job's updated_at so the source hash differs.
    with SessionLocal() as db:
        job = db.get(JobPosting, ids["job_id"])
        assert job is not None
        job.updated_at = datetime.now(UTC)
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(BossCommunicateBlockedError) as exc_info:
            await run_boss_communicate_execute(
                db,
                current_user=user,
                job_id=ids["job_id"],
                application_id=application_id,
                action_id=action_id,
            )
    assert exc_info.value.reason == "source_stale"


@pytest.mark.asyncio
async def test_execute_cross_user_action_returns_404(client) -> None:
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id
        action_id = action.id

    other_user = _get_other_user(ids["other_user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await run_boss_communicate_execute(
                db,
                current_user=other_user,
                job_id=ids["job_id"],
                application_id=application_id,
                action_id=action_id,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_execute_wrong_action_type_returns_404(client) -> None:
    """If the action_id refers to a non-boss_immediate_communicate action,
    the execute endpoint returns 404."""
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    # Prepare a communicate action (this also creates the application).
    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
        )
        application_id = action.application_id

    # Create a platform_submit action on the same application.
    from app.db.repositories import application_action_repo
    from app.schemas.application_action import (
        ApplicationActionPreview,
        ApplicationActionSourceSnapshot,
    )
    from app.services.approval_boundary import compute_payload_hash

    preview = ApplicationActionPreview(
        action_type="platform_submit",
        target_platform="boss",
        target_resource=ids["job_id"],
        selected_artifact_ids=[],
        outgoing_text="您好",
        resume_file_reference=ids["resume_version_id"],
    )
    payload_hash = compute_payload_hash(preview)
    snapshot = ApplicationActionSourceSnapshot(
        job_id=ids["job_id"],
        resume_version_id=ids["resume_version_id"],
        artifact_ids=[],
        source_hash="sha256:dummy",
    )
    with SessionLocal() as db:
        other_action = application_action_repo.create(
            db,
            application_id=application_id,
            user_id=ids["user_id"],
            action_type="platform_submit",
            status="approval_required",
            payload_preview=preview.model_dump(mode="json"),
            payload_hash=payload_hash,
            source_snapshot=snapshot.model_dump(),
        )
        db.commit()
        other_action_id = other_action.id

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await run_boss_communicate_execute(
                db,
                current_user=user,
                job_id=ids["job_id"],
                application_id=application_id,
                action_id=other_action_id,
            )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Human-review override (PRD R1) — AC1 positive
# ---------------------------------------------------------------------------

_HUMAN_MESSAGE = (
    "您好，我是一名有5年Python后端开发经验的工程师，"
    "对贵司的后端工程师职位非常感兴趣，希望能进一步沟通。"
)


@pytest.mark.asyncio
async def test_prepare_human_review_override_needs_review_succeeds(client) -> None:
    """AC1: needs_review + valid human message + acknowledged → prepare succeeds.

    The action's ``outgoing_text`` is the human message (not the artifact's
    null opening_message), the status is ``approval_required``, and
    ``source_snapshot`` carries the ``human_review`` audit key.
    """
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    # Seed a needs_review artifact directly (post-gate shape: opening_message=None).
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    override = HumanReviewOverride(
        opening_message=_HUMAN_MESSAGE,
        acknowledged=True,
        draft_source="human_written",
    )

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
            human_review=override,
        )

    assert action.action_type == "boss_immediate_communicate"
    assert action.status == "approval_required"
    assert action.payload_hash.startswith("sha256:")
    # The outgoing text is the human message, not the artifact's null.
    assert action.payload_preview["outgoing_text"] == _HUMAN_MESSAGE
    # Audit key present.
    snap = action.source_snapshot
    assert snap["human_review"]["acknowledged"] is True
    assert snap["human_review"]["actor_user_id"] == ids["user_id"]
    assert snap["human_review"]["draft_source"] == "human_written"
    assert "acknowledged_at" in snap["human_review"]
    # Staleness hash is unaffected by the audit key.
    assert "source_hash" in snap


@pytest.mark.asyncio
async def test_prepare_human_review_override_then_approve_execute(client, monkeypatch) -> None:
    """AC1 end-to-end: override prepare → approve → execute succeeds.

    The override path converges with the normal path at ``outgoing_text``, so
    approve→execute is unchanged.
    """
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    override = HumanReviewOverride(
        opening_message=_HUMAN_MESSAGE,
        acknowledged=True,
    )

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
            human_review=override,
        )
        application_id = action.application_id
        action_id = action.id

    _approve_action(action_id, application_id, ids["user_id"])

    from app.platforms.boss.fake_adapter import FakeBossAdapter

    fake = FakeBossAdapter(scenario="communicate_succeeded")
    monkeypatch.setattr(
        "app.services.boss_communicate_service.get_adapter",
        lambda: fake,
    )

    with SessionLocal() as db:
        record, action_out, _ = await run_boss_communicate_execute(
            db,
            current_user=user,
            job_id=ids["job_id"],
            application_id=application_id,
            action_id=action_id,
        )

    assert action_out.external_result_status == "submitted"
    assert len(fake.communicate_calls) == 1
    # The adapter received the human message.
    assert fake.communicate_calls[0].opening_message == _HUMAN_MESSAGE


# ---------------------------------------------------------------------------
# Human-review override — AC2 422 matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_human_review_on_communicate_returns_422(client) -> None:
    """AC2: communicate + override → 422 (override not applicable)."""
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    artifact_id = await _run_match(ids)  # communicate decision
    user = _get_user(ids["user_id"])

    override = HumanReviewOverride(opening_message=_HUMAN_MESSAGE, acknowledged=True)

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
                human_review=override,
            )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "human_review_not_applicable"


@pytest.mark.asyncio
async def test_prepare_human_review_override_skip_succeeds(client) -> None:
    """R6: skip + valid human message + acknowledged → prepare succeeds.

    A human may explicitly take responsibility for a model-flagged non-match
    (``skip``); the outgoing text is the human message, the status is
    ``approval_required``, and the audit key records the override. Only
    ``communicate`` + override remains a 422.
    """
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="skip",
        score=0.25,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    override = HumanReviewOverride(opening_message=_HUMAN_MESSAGE, acknowledged=True)

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
            human_review=override,
        )

    assert action.status == "approval_required"
    assert action.payload_preview["outgoing_text"] == _HUMAN_MESSAGE
    snap = action.source_snapshot
    assert snap["human_review"]["acknowledged"] is True
    assert snap["human_review"]["actor_user_id"] == ids["user_id"]


@pytest.mark.asyncio
async def test_prepare_human_review_message_too_short_returns_422(client) -> None:
    """AC2: human message too short → 422 (human_review_message_invalid)."""
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    override = HumanReviewOverride(opening_message="您好", acknowledged=True)

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
                human_review=override,
            )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "human_review_message_invalid"


@pytest.mark.asyncio
async def test_prepare_human_review_message_with_pii_returns_422(client) -> None:
    """AC2: human message containing a phone number → 422."""
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    pii_message = (
        "您好，我是一名有5年Python后端开发经验的工程师，"
        "我的手机号是13812345678，希望能进一步沟通。"
    )
    override = HumanReviewOverride(opening_message=pii_message, acknowledged=True)

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
                human_review=override,
            )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "human_review_message_invalid"


@pytest.mark.asyncio
async def test_prepare_human_review_message_excessive_punctuation_returns_422(
    client,
) -> None:
    """AC2: human message with excessive punctuation → 422."""
    from app.schemas.boss_communicate import HumanReviewOverride

    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    # Excessive exclamation marks trigger the tone guard.
    shouty_message = (
        "您好您好您好您好您好您好您好您好您好您好"
        "！！！！！！！！！！！！！！！！！！！！"
    )
    override = HumanReviewOverride(opening_message=shouty_message, acknowledged=True)

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
                human_review=override,
            )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "human_review_message_invalid"


@pytest.mark.asyncio
async def test_prepare_human_review_acknowledged_false_rejected_by_schema(client) -> None:
    """AC2: ``acknowledged`` must be the literal ``True`` — the schema rejects
    anything else before the service is reached."""
    from pydantic import ValidationError

    from app.schemas.boss_communicate import HumanReviewOverride

    # acknowledged=False is not a valid Literal[True].
    with pytest.raises(ValidationError):
        HumanReviewOverride(opening_message=_HUMAN_MESSAGE, acknowledged=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Human-review override — AC3 draft field (match response carries pre-gate draft)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_downgraded_returns_draft_opening_message(client) -> None:
    """AC3: when the safety gate downgrades communicate → needs_review, the
    service returns the pre-gate draft as ``raw_opening_message`` (the 5th
    element of the tuple). The persisted artifact's opening_message is None."""
    from unittest.mock import patch

    from app.models_gateway.fake import _BOSS_MATCH_FAKE_OUTPUTS, FakeModelGateway

    ids = _seed()
    user = _get_user(ids["user_id"])

    draft_msg = "您好，我对该职位非常感兴趣，希望能进一步沟通。"
    low_score_output = {
        "decision": "communicate",
        "score": 0.45,
        "reasons": ["Weak match"],
        "risks": [],
        "missing_requirements": [],
        "opening_message": draft_msg,
    }
    with patch(
        "app.models_gateway.fake._BOSS_MATCH_FAKE_OUTPUTS",
        {
            "communicate": low_score_output,
            "skip": _BOSS_MATCH_FAKE_OUTPUTS["skip"],
            "needs_review": _BOSS_MATCH_FAKE_OUTPUTS["needs_review"],
        },
    ):
        with SessionLocal() as db:
            _, artifact, execution, downgraded, raw_opening_message = (
                await run_boss_match_decision(
                    db,
                    user,
                    job_id=ids["job_id"],
                    resume_version_id=ids["resume_version_id"],
                    gateway=FakeModelGateway(),
                )
            )

    assert downgraded is True
    assert execution.output.decision.value == "needs_review"
    assert execution.output.opening_message is None
    # The pre-gate draft is returned for the human-review UI.
    assert raw_opening_message == draft_msg
    # The persisted artifact stores the post-gate output (no opening_message).
    assert "opening_message" not in artifact.content or (
        json.loads(artifact.content)["opening_message"] is None
    )


@pytest.mark.asyncio
async def test_match_not_downgraded_returns_null_draft(client) -> None:
    """AC3: when no downgrade happens, ``raw_opening_message`` is None."""
    from app.models_gateway.fake import FakeModelGateway

    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        _, _, _, downgraded, raw_opening_message = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )

    assert downgraded is False
    assert raw_opening_message is None


# ---------------------------------------------------------------------------
# Human-review override — AC5 no-override regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_no_human_review_field_is_identical_to_before(client) -> None:
    """AC5: when ``human_review`` is omitted (default None), the prepare path
    behaves exactly as before — communicate required, opening_message
    re-validated, no ``human_review`` key in source_snapshot."""
    ids = _seed()
    artifact_id = await _run_match(ids)
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        action = prepare_communicate_action(
            db,
            user,
            ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            match_artifact_id=artifact_id,
            # human_review omitted — default None
        )

    assert action.status == "approval_required"
    snap = action.source_snapshot
    assert "human_review" not in snap
    # The outgoing text is the artifact's opening message.
    assert action.payload_preview["outgoing_text"] is not None
    assert len(action.payload_preview["outgoing_text"]) >= 10


@pytest.mark.asyncio
async def test_prepare_no_human_review_needs_review_still_returns_422(client) -> None:
    """AC5: without the override, needs_review still 422s (the dead-end is
    only escapeable via the explicit human_review path)."""
    ids = _seed()
    artifact_id = _seed_artifact_directly(
        ids,
        decision="needs_review",
        score=0.55,
        opening_message=None,
    )
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            prepare_communicate_action(
                db,
                user,
                ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                match_artifact_id=artifact_id,
            )
    assert exc_info.value.status_code == 422
