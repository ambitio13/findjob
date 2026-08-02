"""API + worker integration tests for the platform guided-submit *prepare* flow.

The prepare endpoint uses the enqueue-and-poll pattern (design.md §API Shape):

- ``POST /applications/{application_id}/platform-submissions/prepare`` validates
  application ownership + status (must be ``materials_ready`` or
  ``approval_required``) + resume-version usability up front (404/422 bubble
  before any run is created), then creates a ``queued`` ``AgentRun``
  (``workflow_type="platform_guided_submit_prepare"``), enqueues a
  ``PlatformGuidedSubmitPreparePayload`` to the worker queue, and returns
  immediately with HTTP 202 + ``PlatformSubmissionPrepareResponse``. It does
  **not** block on the adapter call.
- The worker handler ``platform_guided_submit_prepare`` executes the prepare
  workflow (``run_platform_guided_submit_prepare_worker``): flips the run
  queued → running → succeeded/failed, persists sanitized ``AgentStep`` rows,
  runs the BOSS adapter in dry-run/fill-only mode, and on success persists a
  ``platform_submit`` ``ApplicationAction`` + a ``platform_preview_ready``
  timeline event, transitioning the application to ``approval_required``.

Tests cover both layers:

API layer (HTTP 202 contract):

- successful submit: 202, ``run.status == "queued"``, ``target_platform ==
  "boss"``, ``mode == "dry_run"``, an ``AgentRun`` row is persisted in
  ``queued`` state with sanitized result (no raw text).
- enqueue failure (Redis down): the run is flipped to ``failed`` before
  returning so the frontend never polls forever.
- duplicate active-run guard → 409.
- 422 for non-preparable status; 404 for missing/cross-user application.

Worker layer (handler execution):

- successful prepare via the handler: run succeeds, ordered steps persisted,
  ``ApplicationAction(platform_submit)`` created with payload hash computed from
  the filled snapshot, application transitions to ``approval_required``,
  ``platform_preview_ready`` timeline event appended, sanitization (no raw
  resume token leaked).
- failure scenario (login_required): run failed, platform_failure event, no
  action created, next_action=manual_review.
- stale source detection → run failed with ``stale_source`` code.
- repeated prepare refreshes the existing action in place (no duplicates).
- ownership mismatch → run failed.

The enqueue path is faked by patching ``app.queue.runtime.get_queue`` so no real
Redis is required. The worker handler is called directly with the typed payload
(or a dict, mirroring how arq delivers jobs). The adapter is the fake by
default; scenarios are injected by patching ``get_adapter``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.models import (
    AgentRun,
    AgentStep,
    ApplicationAction,
    ApplicationRecord,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.platforms.base import PREPARE_FAILURE_CODES, PrepareOutcome
from app.platforms.boss.fake_adapter import FakeBossAdapter
from app.queue.handlers import platform_guided_submit_prepare
from app.queue.payloads import PlatformGuidedSubmitPreparePayload

SECRET = "SUPER_SECRET_RESUME_TOKEN_42"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "pf_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name=f"用户 {user_id}")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session,
    user_id: str,
    raw_text: str,
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


def _make_application(
    db: Session,
    user_id: str,
    job_id: str,
    resume_version_id: str,
    *,
    status: str = "materials_ready",
) -> ApplicationRecord:
    record = ApplicationRecord(
        user_id=user_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        status=status,
        timeline=[],
    )
    db.add(record)
    db.flush()
    return record


def _seed(
    user_id: str = "pf_user",
    *,
    raw_text: str = "张三\nPython 5年 FastAPI",
    jd_raw: str = "Senior Python backend engineer. Build APIs with FastAPI.",
    status: str = "materials_ready",
) -> dict[str, str]:
    """Seed a user + resume + version + job + application, returning their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id, raw_text)
        job = _make_job(db, user_id, jd_raw=jd_raw)
        app = _make_application(db, user_id, job.id, version.id, status=status)
        db.commit()
        return {
            "user_id": user_id,
            "resume_id": resume.id,
            "resume_version_id": version.id,
            "job_id": job.id,
            "application_id": app.id,
        }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _prepare(
    client: TestClient,
    application_id: str,
    user: str,
    *,
    target_resource: str = "https://boss.zhipin.com/job/123",
    outgoing_text: str | None = "您好，我对这个岗位很感兴趣。",
    selected_artifact_ids: list[str] | None = None,
    resume_file_reference: str | None = "resume-v1",
) -> Any:
    return client.post(
        f"/api/v1/applications/{application_id}/platform-submissions/prepare",
        headers=_headers(user),
        json={
            "target_resource": target_resource,
            "outgoing_text": outgoing_text,
            "selected_artifact_ids": selected_artifact_ids or [],
            "resume_file_reference": resume_file_reference,
        },
    )


def _fake_enqueue_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.enqueue_job.return_value = object()
    return pool


def _patch_get_queue_ok() -> Any:
    return patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(return_value=_fake_enqueue_pool()),
    )


# ---------------------------------------------------------------------------
# Source-hash recompute helper (mirrors the service)
# ---------------------------------------------------------------------------


def _compute_source_hash_for(
    user_id: str,
    job_id: str,
    resume_version_id: str,
) -> str:
    from app.services.application_state import build_source_snapshot
    from app.services.platform_submission_service import _PROMPT_VERSIONS

    with SessionLocal() as db:
        job = db.get(JobPosting, job_id)
        version = db.get(ResumeVersion, resume_version_id)
        profile = db.get(UserProfile, user_id)
        assert job is not None and version is not None and profile is not None
        snapshot = build_source_snapshot(
            job_id=job.id,
            job_updated_at=job.updated_at,
            resume_version_id=version.id,
            resume_version_no=version.version_no,
            profile_updated_at=profile.updated_at,
            prompt_versions=_PROMPT_VERSIONS,
        )
        return snapshot.source_hash


def _create_queued_prepare_run(
    user_id: str,
    application_id: str,
    job_id: str,
    resume_version_id: str,
    source_hash: str,
    *,
    target_resource: str = "https://boss.zhipin.com/job/123",
    selected_artifact_ids: list[str] | None = None,
    outgoing_text: str | None = "您好，我对这个岗位很感兴趣。",
    resume_file_reference: str | None = "resume-v1",
) -> str:
    """Insert a queued platform_guided_submit_prepare AgentRun and return its id."""
    with SessionLocal() as db:
        from app.db.repositories import agent_run_repo

        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="platform_guided_submit_prepare",
            status="queued",
            job_id=job_id,
            result={
                "user_id": user_id,
                "application_id": application_id,
                "job_id": job_id,
                "resume_version_id": resume_version_id,
                "source_hash": source_hash,
                "target_platform": "boss",
                "target_resource": target_resource,
                "selected_artifact_ids": selected_artifact_ids or [],
                "mode": "dry_run",
            },
        )
        db.commit()
        return run.id


def _make_payload(
    run_id: str,
    user_id: str,
    application_id: str,
    job_id: str,
    resume_version_id: str,
    source_hash: str,
    *,
    target_resource: str = "https://boss.zhipin.com/job/123",
    selected_artifact_ids: list[str] | None = None,
    outgoing_text: str | None = "您好，我对这个岗位很感兴趣。",
    resume_file_reference: str | None = "resume-v1",
) -> PlatformGuidedSubmitPreparePayload:
    return PlatformGuidedSubmitPreparePayload(
        workflow_type="platform_guided_submit_prepare",
        user_id=user_id,
        agent_run_id=run_id,
        idempotency_key=f"platform_guided_submit_prepare:{run_id}",
        application_id=application_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        source_hash=source_hash,
        selected_artifact_ids=selected_artifact_ids or [],
        outgoing_text=outgoing_text,
        resume_file_reference=resume_file_reference,
        target_resource=target_resource,
    )


def _patch_fake_adapter(scenario: str = "filled_preview") -> Any:
    """Patch the registry to return a fake adapter with the given scenario."""
    return patch(
        "app.platforms.boss.registry.get_adapter",
        return_value=FakeBossAdapter(scenario=scenario),
    )


# ---------------------------------------------------------------------------
# API layer: successful submit (HTTP 202, queued run, sanitized metadata)
# ---------------------------------------------------------------------------


def test_prepare_returns_202_with_queued_run(client: TestClient) -> None:
    ids = _seed("pf_ok")
    with _patch_get_queue_ok():
        resp = _prepare(client, ids["application_id"], "pf_ok")
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["application_id"] == ids["application_id"]
    assert body["target_platform"] == "boss"
    assert body["mode"] == "dry_run"
    run = body["run"]
    assert run["status"] == "queued"
    run_id = run["id"]
    assert run_id

    with SessionLocal() as db:
        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "queued"
        assert run_row.workflow_type == "platform_guided_submit_prepare"
        assert run_row.user_id == "pf_ok"
        assert run_row.job_id == ids["job_id"]
        assert run_row.result["application_id"] == ids["application_id"]
        assert run_row.result["source_hash"]
        assert run_row.result["target_resource"] == "https://boss.zhipin.com/job/123"
        # Sanitization: no raw text keys in the persisted result.
        assert "jd_raw" not in run_row.result
        assert "raw_text" not in run_row.result
        assert "outgoing_text" not in run_row.result


# ---------------------------------------------------------------------------
# API layer: enqueue failure flips run to failed
# ---------------------------------------------------------------------------


def test_prepare_enqueue_failure_flips_run_to_failed(client: TestClient) -> None:
    ids = _seed("pf_redis")
    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = _prepare(client, ids["application_id"], "pf_redis")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["run"]["status"] == "failed"
    assert body["run"]["error"] == "queue enqueue failed"

    run_id = body["run"]["id"]
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "queue enqueue failed"


# ---------------------------------------------------------------------------
# API layer: duplicate active-run guard (409)
# ---------------------------------------------------------------------------


def test_prepare_duplicate_while_active_returns_409(client: TestClient) -> None:
    ids = _seed("pf_dup")
    with _patch_get_queue_ok():
        first = _prepare(client, ids["application_id"], "pf_dup")
    assert first.status_code == 202, first.text
    assert first.json()["run"]["status"] == "queued"

    with _patch_get_queue_ok():
        second = _prepare(client, ids["application_id"], "pf_dup")
    assert second.status_code == 409, second.text
    assert "already in progress" in second.json()["detail"]


def test_prepare_after_terminal_allows_reprepare(client: TestClient) -> None:
    ids = _seed("pf_regen")
    with _patch_get_queue_ok():
        first = _prepare(client, ids["application_id"], "pf_regen")
    assert first.status_code == 202
    run_id = first.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.status = "succeeded"
        db.commit()

    with _patch_get_queue_ok():
        second = _prepare(client, ids["application_id"], "pf_regen")
    assert second.status_code == 202, second.text


# ---------------------------------------------------------------------------
# API layer: status validation
# ---------------------------------------------------------------------------


def test_prepare_rejects_non_preparable_status(client: TestClient) -> None:
    ids = _seed("pf_status", status="planned")
    with _patch_get_queue_ok():
        resp = _prepare(client, ids["application_id"], "pf_status")
    assert resp.status_code == 422, resp.text
    assert "materials_ready" in resp.json()["detail"]


def test_prepare_rejects_approval_required_status_allows(client: TestClient) -> None:
    """approval_required is also a preparable status (refresh the preview)."""
    ids = _seed("pf_approval", status="approval_required")
    with _patch_get_queue_ok():
        resp = _prepare(client, ids["application_id"], "pf_approval")
    assert resp.status_code == 202, resp.text


def test_prepare_404_missing_application(client: TestClient) -> None:
    _seed("pf_404")
    with _patch_get_queue_ok():
        resp = _prepare(client, "nonexistent-app", "pf_404")
    assert resp.status_code == 404, resp.text


def test_prepare_404_cross_user(client: TestClient) -> None:
    ids = _seed("pf_owner")
    _seed("pf_other")
    with _patch_get_queue_ok():
        resp = _prepare(client, ids["application_id"], "pf_other")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Worker layer: successful prepare (filled_preview)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_prepare_success_creates_action_and_approval_required() -> None:
    ids = _seed("pf_w_ok")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )

    with _patch_fake_adapter("filled_preview"):
        result = await platform_guided_submit_prepare({}, payload)

    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.result["action_id"]
        assert run.result["payload_hash"].startswith("sha256:")
        assert run.result["external_idempotency_key"].startswith(
            f"{ids['application_id']}:platform_submit:"
        )
        # Sanitization: no raw text in the run result.
        result_json = str(run.result)
        assert SECRET not in result_json
        assert "outgoing_text" not in run.result

        # Steps: 8 ordered succeeded steps.
        steps = (
            db.execute(
                select(AgentStep).where(AgentStep.run_id == run_id)
            )
            .scalars()
            .all()
        )
        assert len(steps) == 8
        assert [s.step_no for s in steps] == list(range(1, 9))
        assert all(s.status == "succeeded" for s in steps)

        # Action: platform_submit, approval_required, with payload hash.
        action = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .first()
        )
        assert action is not None
        assert action.action_type == "platform_submit"
        assert action.status == "approval_required"
        assert action.payload_hash.startswith("sha256:")
        assert action.external_idempotency_key.startswith(
            f"{ids['application_id']}:platform_submit:"
        )
        # Sanitization: no raw secret in the action preview.
        assert SECRET not in str(action.payload_preview)

        # Application transitioned to approval_required.
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.status == "approval_required"

        # Timeline: platform_preview_ready event.
        timeline = app.timeline or []
        preview_events = [e for e in timeline if e.get("type") == "platform_preview_ready"]
        assert len(preview_events) == 1
        assert preview_events[0]["metadata"]["action_id"] == action.id
        assert preview_events[0]["metadata"]["target_platform"] == "boss"


@pytest.mark.asyncio
async def test_worker_prepare_failure_login_required() -> None:
    ids = _seed("pf_w_login")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )

    with _patch_fake_adapter("login_required"):
        result = await platform_guided_submit_prepare({}, payload)

    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        # No action created on failure.
        action = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .first()
        )
        assert action is None

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["category"] == "platform"
        assert app.latest_error["code"] == "platform_login_required"
        assert app.latest_error["next_action"] == "manual_review"

        timeline = app.timeline or []
        fail_events = [e for e in timeline if e.get("type") == "platform_failure"]
        assert len(fail_events) == 1
        assert fail_events[0]["metadata"]["error_code"] == "platform_login_required"


# ---------------------------------------------------------------------------
# Worker layer: prepare failure matrix (all non-filled PrepareOutcome values)
# ---------------------------------------------------------------------------

#: All non-filled prepare outcomes covered by the failure matrix. ``login_required``
#: is duplicated by the dedicated test above but is kept here so the parametrized
#: case stays exhaustive and self-documenting.
_PREPARE_FAILURE_SCENARIOS = [
    outcome
    for outcome in PrepareOutcome
    if outcome is not PrepareOutcome.filled_preview
]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", _PREPARE_FAILURE_SCENARIOS)
async def test_worker_prepare_failure_matrix(
    outcome: PrepareOutcome,
) -> None:
    """Every non-filled prepare outcome persists a sanitized failure envelope.

    Asserts the adapter classification flows through to:
    - run failed, no ``platform_submit`` action created;
    - ``ApplicationFailureEnvelope`` with ``category=platform`` and the code +
      next-action derived from ``PREPARE_FAILURE_CODES``;
    - a ``platform_failure`` timeline event tagged with the error code;
    - no raw cookies/tokens/credentials/raw JD/raw resume/page HTML persisted.
    """
    scenario = outcome.value
    expected_code, expected_next_action = PREPARE_FAILURE_CODES[outcome]

    ids = _seed(
        f"pf_w_matrix_{scenario}",
        raw_text=f"张三\n{SECRET}\nPython 5年",
        jd_raw=f"Senior engineer. Token {SECRET}.",
    )
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )

    with _patch_fake_adapter(scenario):
        result = await platform_guided_submit_prepare({}, payload)

    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        # No platform_submit action created on a prepare failure.
        action = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .first()
        )
        assert action is None

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        # Sanitized failure envelope.
        assert app.latest_error is not None
        assert app.latest_error["category"] == "platform"
        assert app.latest_error["code"] == expected_code
        assert app.latest_error["next_action"] == expected_next_action

        # Timeline: exactly one platform_failure event tagged with the code.
        timeline = app.timeline or []
        fail_events = [e for e in timeline if e.get("type") == "platform_failure"]
        assert len(fail_events) == 1
        assert fail_events[0]["metadata"]["error_code"] == expected_code

        # Sanitization: no raw secret / raw JD / page HTML in any persisted row.
        steps = (
            db.execute(select(AgentStep).where(AgentStep.run_id == run_id))
            .scalars()
            .all()
        )
        for row in [run, app, *steps]:
            assert SECRET not in str(row.__dict__), (
                f"raw secret leaked into {type(row).__name__} row "
                f"for scenario={scenario}"
            )


@pytest.mark.asyncio
async def test_worker_prepare_stale_source_detected() -> None:
    ids = _seed("pf_w_stale")
    # Enqueue with a bogus source hash so the worker detects staleness.
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "sha256:stale-enqueue-hash",
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "sha256:stale-enqueue-hash",
    )

    with _patch_fake_adapter("filled_preview"):
        result = await platform_guided_submit_prepare({}, payload)

    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["category"] == "data"
        assert app.latest_error["code"] == "stale_source"
        assert app.latest_error["next_action"] == "edit_source"

        # No action created.
        action = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .first()
        )
        assert action is None


@pytest.mark.asyncio
async def test_worker_prepare_ownership_mismatch_fails_run() -> None:
    ids = _seed("pf_w_owner")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    # Payload claims a different user than the run's owner.
    payload = _make_payload(
        run_id,
        "attacker-user",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )

    with _patch_fake_adapter("filled_preview"):
        result = await platform_guided_submit_prepare({}, payload)

    assert result == "ownership_mismatch"

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "ownership mismatch"


@pytest.mark.asyncio
async def test_worker_prepare_dict_payload_works() -> None:
    """The handler accepts a dict payload (mirrors how arq delivers jobs)."""
    ids = _seed("pf_w_dict")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    ).model_dump()

    with _patch_fake_adapter("filled_preview"):
        result = await platform_guided_submit_prepare({}, payload)

    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_worker_repeated_prepare_refreshes_action_in_place() -> None:
    """A second prepare on the same application reuses the existing action."""
    ids = _seed("pf_w_refresh")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )

    # First prepare.
    run_id_1 = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload_1 = _make_payload(
        run_id_1,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    with _patch_fake_adapter("filled_preview"):
        await platform_guided_submit_prepare({}, payload_1)

    with SessionLocal() as db:
        actions_1 = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .all()
        )
        assert len(actions_1) == 1
        first_action_id = actions_1[0].id

    # Second prepare (same payload, different run).
    run_id_2 = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload_2 = _make_payload(
        run_id_2,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    with _patch_fake_adapter("filled_preview"):
        await platform_guided_submit_prepare({}, payload_2)

    with SessionLocal() as db:
        actions_2 = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .all()
        )
        # Still exactly one action — refreshed in place.
        assert len(actions_2) == 1
        assert actions_2[0].id == first_action_id
        assert actions_2[0].status == "approval_required"


@pytest.mark.asyncio
async def test_worker_prepare_no_raw_secret_in_any_persisted_row() -> None:
    """Sanitization: the raw resume token never appears in any persisted row."""
    ids = _seed("pf_w_sanitize", raw_text=f"张三\n{SECRET}\nPython 5年")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )

    with _patch_fake_adapter("filled_preview"):
        await platform_guided_submit_prepare({}, payload)

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        steps = (
            db.execute(select(AgentStep).where(AgentStep.run_id == run_id))
            .scalars()
            .all()
        )
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        actions = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .all()
        )

        for row in [run, app, *steps, *actions]:
            dumped = str(row.__dict__)
            assert SECRET not in dumped, (
                f"raw secret leaked into {type(row).__name__} row"
            )


# ===========================================================================
# Final submit behind guards (Step 5)
# ===========================================================================
#
# The submit endpoint runs synchronously (not a queue worker). It loads the
# succeeded prepare run, recomputes the current payload + source hashes, calls
# ``assert_action_approved`` (the hard approval boundary), checks the external
# idempotency key, then calls ``adapter.submit_prepared`` — the only external
# side effect.
#
# Tests cover:
#
# - submit without approval → 422 (application not in ``approved`` status) /
#   409 (action not approved, depending on the order of guards).
# - submit with approval → ``submitted`` application status +
#   ``platform_submit_succeeded`` timeline event + sanitized action result.
# - payload hash mismatch blocks submit → 409.
# - duplicate idempotency key returns the existing terminal result without
#   touching the platform (``submit_calls`` stays empty on the second call).
# - abort revokes the action and appends ``platform_submit_blocked``.
# - abort is refused (409) once a terminal result exists.
# - every submit failure outcome (duplicate_detected / unknown /
#   platform_failure) persists a sanitized failure envelope and never leaks the
#   raw resume secret.
# - no raw secret appears in any persisted row after a submit/abort.


def _seed_approved_for_submit(
    user_id: str = "pf_sub",
    *,
    raw_text: str = "张三\nPython 5年 FastAPI",
    scenario: str = "submitted",
) -> dict[str, str]:
    """Seed a user + resume + job + application, run a successful prepare, and
    approve the resulting action so the application is in ``approved`` status.

    Returns the ids dict augmented with ``run_id`` and ``action_id``. The fake
    adapter scenario defaults to ``submitted`` so a follow-up submit succeeds.
    """
    ids = _seed(user_id, raw_text=raw_text, status="materials_ready")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    with _patch_fake_adapter("filled_preview"):
        import asyncio

        asyncio.run(platform_guided_submit_prepare({}, payload))

    with SessionLocal() as db:
        action = (
            db.execute(
                select(ApplicationAction).where(
                    ApplicationAction.application_id == ids["application_id"]
                )
            )
            .scalars()
            .first()
        )
        assert action is not None
        action_id = action.id
        # Approve the action via the approval boundary so the application
        # transitions to ``approved``.
        from app.db.models.models import UserProfile as _UP
        from app.services import approval_action_service

        user = db.get(_UP, ids["user_id"])
        approval_action_service.approve_action(
            db, user, ids["application_id"], action_id
        )

        # Transition the application to ``approved`` (the approval service sets
        # the action status but not the application status; the design relies on
        # the application status machine for the submit guard).
        from app.services import application_service

        application_service.update_application_status(
            db,
            user,
            ids["application_id"],
            new_status="approved",
            note="user approved the platform submission",
        )
        db.commit()

    ids["run_id"] = run_id
    ids["action_id"] = action_id
    # Stash the scenario so the caller can patch the adapter.
    ids["_scenario"] = scenario
    return ids


def _submit(
    client: TestClient,
    application_id: str,
    run_id: str,
    user: str,
) -> Any:
    return client.post(
        f"/api/v1/applications/{application_id}/platform-submissions/{run_id}/submit",
        headers=_headers(user),
    )


def _abort(
    client: TestClient,
    application_id: str,
    run_id: str,
    user: str,
) -> Any:
    return client.post(
        f"/api/v1/applications/{application_id}/platform-submissions/{run_id}/abort",
        headers=_headers(user),
    )


# ---------------------------------------------------------------------------
# Submit: success
# ---------------------------------------------------------------------------


def test_submit_with_approval_transitions_to_submitted(client: TestClient) -> None:
    ids = _seed_approved_for_submit("pf_sub_ok")
    with _patch_fake_adapter("submitted"):
        resp = _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["application_id"] == ids["application_id"]
    assert body["action"]["external_result"]["result_status"] == "submitted"
    assert body["action"]["external_result_status"] == "submitted"

    with SessionLocal() as db:
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.status == "submitted"

        timeline = app.timeline or []
        succeeded = [e for e in timeline if e.get("type") == "platform_submit_succeeded"]
        assert len(succeeded) == 1
        assert succeeded[0]["metadata"]["action_id"] == ids["action_id"]

        run = db.get(AgentRun, ids["run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.result["submit_outcome"] == "submitted"


# ---------------------------------------------------------------------------
# Submit: blocked by approval guard
# ---------------------------------------------------------------------------


def test_submit_without_approval_returns_409(client: TestClient) -> None:
    """If the action was never approved, the approval guard blocks the submit."""
    ids = _seed("pf_sub_noappr", status="materials_ready")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_prepare_run(
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    payload = _make_payload(
        run_id,
        ids["user_id"],
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        source_hash,
    )
    with _patch_fake_adapter("filled_preview"):
        import asyncio

        asyncio.run(platform_guided_submit_prepare({}, payload))

    # Application is still ``approval_required`` — the submit guard rejects it
    # with 422 (status not approved) before the approval boundary is reached.
    with _patch_fake_adapter("submitted"):
        resp = _submit(client, ids["application_id"], run_id, ids["user_id"])
    assert resp.status_code == 422, resp.text
    assert "approved" in resp.json()["detail"]


def test_submit_payload_hash_mismatch_returns_409(client: TestClient) -> None:
    """If the payload changed after approval, the submit is blocked with 409."""
    ids = _seed_approved_for_submit("pf_sub_hash")
    # Tamper with the action's payload preview so the recomputed hash differs
    # from the approved hash.
    with SessionLocal() as db:
        action = db.get(ApplicationAction, ids["action_id"])
        assert action is not None
        preview = dict(action.payload_preview)
        preview["outgoing_text"] = "完全不同的开场白"
        action.payload_preview = preview
        db.commit()

    with _patch_fake_adapter("submitted"):
        resp = _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["reason"] == "payload_hash_mismatch"


# ---------------------------------------------------------------------------
# Submit: idempotency — replay returns existing terminal result
# ---------------------------------------------------------------------------


def test_submit_idempotent_replay_returns_existing_result(client: TestClient) -> None:
    """A second submit with the same idempotency key returns the existing
    terminal result without touching the platform again."""
    ids = _seed_approved_for_submit("pf_sub_idem")

    adapter = FakeBossAdapter(scenario="submitted")
    with patch("app.platforms.boss.registry.get_adapter", return_value=adapter):
        first = _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert first.status_code == 200, first.text
    assert len(adapter.submit_calls) == 1

    # Second submit — the idempotency guard should short-circuit.
    with patch("app.platforms.boss.registry.get_adapter", return_value=adapter):
        second = _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert second.status_code == 200, second.text
    # The adapter was NOT called a second time.
    assert len(adapter.submit_calls) == 1

    # Both responses carry the same terminal result.
    assert first.json()["action"]["external_result_status"] == "submitted"
    assert second.json()["action"]["external_result_status"] == "submitted"

    with SessionLocal() as db:
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        blocked = [
            e for e in (app.timeline or []) if e.get("type") == "platform_submit_blocked"
        ]
        assert any(e["metadata"]["reason"] == "idempotency_replay" for e in blocked)


# ---------------------------------------------------------------------------
# Submit: failure outcomes (duplicate / unknown / platform_failure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "result_status", "event_type"),
    [
        ("submit_duplicate_detected", "duplicate", "platform_failure"),
        ("submit_unknown", "unknown", "platform_submit_unknown"),
        ("platform_failure", "failed", "platform_failure"),
    ],
)
def test_submit_failure_outcomes_persist_sanitized_envelope(
    client: TestClient,
    scenario: str,
    result_status: str,
    event_type: str,
) -> None:
    ids = _seed_approved_for_submit(
        f"pf_sub_{scenario}",
        raw_text=f"张三\n{SECRET}\nPython 5年",
        scenario=scenario,
    )
    with _patch_fake_adapter(scenario):
        resp = _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"]["external_result_status"] == result_status

    with SessionLocal() as db:
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        # The application did NOT transition to submitted.
        assert app.status != "submitted"
        # A failure envelope was persisted.
        assert app.latest_error is not None
        assert app.latest_error["category"] == "platform"
        # The expected timeline event was appended.
        events = [e for e in (app.timeline or []) if e.get("type") == event_type]
        assert len(events) == 1

        run = db.get(AgentRun, ids["run_id"])
        assert run is not None
        assert run.status == "failed"

        # Sanitization: no raw secret in any persisted row.
        action = db.get(ApplicationAction, ids["action_id"])
        for row in [run, app, action]:
            assert SECRET not in str(row.__dict__), (
                f"raw secret leaked into {type(row).__name__} row"
            )


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------


def test_abort_revokes_action_before_submit(client: TestClient) -> None:
    """Aborting before any submit revokes the action and appends a blocked event."""
    ids = _seed_approved_for_submit("pf_abort_ok")
    resp = _abort(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"]["status"] == "revoked"
    assert body["action"]["approval"] is None

    with SessionLocal() as db:
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        blocked = [
            e for e in (app.timeline or []) if e.get("type") == "platform_submit_blocked"
        ]
        assert len(blocked) == 1
        assert blocked[0]["metadata"]["reason"] == "user_aborted"
        assert blocked[0]["actor"] == "user"


def test_abort_refused_after_terminal_result(client: TestClient) -> None:
    """Once a terminal result exists, abort is refused with 409."""
    ids = _seed_approved_for_submit("pf_abort_term")
    with _patch_fake_adapter("submitted"):
        _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])

    resp = _abort(client, ids["application_id"], ids["run_id"], ids["user_id"])
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Submit: 404 / cross-user
# ---------------------------------------------------------------------------


def test_submit_404_missing_run(client: TestClient) -> None:
    ids = _seed_approved_for_submit("pf_sub_404")
    resp = _submit(
        client, ids["application_id"], "nonexistent-run", ids["user_id"]
    )
    assert resp.status_code == 404, resp.text


def test_submit_404_cross_user(client: TestClient) -> None:
    ids = _seed_approved_for_submit("pf_sub_owner")
    _seed("pf_sub_other")
    with _patch_fake_adapter("submitted"):
        resp = _submit(
            client, ids["application_id"], ids["run_id"], "pf_sub_other"
        )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Submit: no raw secret in any persisted row (sanitization)
# ---------------------------------------------------------------------------


def test_submit_no_raw_secret_in_any_persisted_row(client: TestClient) -> None:
    ids = _seed_approved_for_submit(
        "pf_sub_sanitize", raw_text=f"张三\n{SECRET}\nPython 5年"
    )
    with _patch_fake_adapter("submitted"):
        _submit(client, ids["application_id"], ids["run_id"], ids["user_id"])

    with SessionLocal() as db:
        run = db.get(AgentRun, ids["run_id"])
        assert run is not None
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        action = db.get(ApplicationAction, ids["action_id"])
        assert action is not None
        steps = (
            db.execute(select(AgentStep).where(AgentStep.run_id == ids["run_id"]))
            .scalars()
            .all()
        )
        for row in [run, app, action, *steps]:
            assert SECRET not in str(row.__dict__), (
                f"raw secret leaked into {type(row).__name__} row"
            )

