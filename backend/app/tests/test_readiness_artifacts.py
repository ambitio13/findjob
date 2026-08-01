"""API + worker integration tests for the readiness artifact generation workflow.

The generate endpoint uses the enqueue-and-poll pattern (design.md):

- ``POST /applications/{application_id}/artifacts/{artifact_type}/generate``
  validates application/job/resume ownership + resume-version usability up front
  (404/422 bubble before any run is created), then creates a ``queued``
  ``AgentRun`` (``workflow_type="readiness_generation"``), enqueues a
  ``ReadinessGenerationPayload`` to the worker, and returns immediately with
  HTTP 202 + ``RunReadinessSubmitResponse``. It does **not** block on the model
  call.
- The worker handler ``readiness_generation`` executes the generation workflow
  (``run_readiness_generation_worker``): flips the run queued → running →
  succeeded/failed, persists six sanitized ``AgentStep`` rows, and on success
  stores a ``GeneratedArtifact`` + appends a timeline event to the
  ``ApplicationRecord`` so the frontend can hydrate by polling
  ``GET /agent-runs/{id}/detail``.

Tests cover both layers:

API layer (HTTP 202 contract):

- successful submit: 202, ``run.status == "queued"``, artifact_type echoed,
  an ``AgentRun`` row is persisted in ``queued`` state with sanitized result
  (jd_raw_len / resume_raw_text_len, not raw text).
- enqueue failure (Redis down): the run is flipped to ``failed`` before
  returning so the frontend never polls forever.
- duplicate submit while a queued/running run exists → 409.
- 404 for missing/cross-user application; 422 for a resume version with no raw
  text; 404 for an application with no resume version bound.
- 422 for an invalid artifact type path segment.

Worker layer (handler execution):

- successful generation for each of the 4 artifact types via the
  ``readiness_generation`` handler: run succeeds, six ordered steps persisted,
  ``GeneratedArtifact`` created with correct ``artifact_type`` +
  ``source_hash`` provenance, timeline ``artifact_generated`` event appended,
  ``readiness_snapshot`` updated, sanitization (no raw resume token leaked).
- queue failure (Redis down at enqueue time) → run flipped to ``failed``.
- model-invalid output (stub gateway) → run failed, validate_model_output step
  failed, no ``GeneratedArtifact`` created.
- gateway raises → run failed, call_model step failed.
- duplicate active run → 409 from the API.
- stale source detection → run failed with ``stale_source`` code.
- sanitization: raw resume text never appears in any persisted row.

The enqueue path is faked by patching ``app.queue.runtime.get_queue`` so no
real Redis is required. The worker handler is called directly with the typed
payload (or a dict, mirroring how arq delivers jobs).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.models import (
    AgentRun,
    AgentStep,
    ApplicationRecord,
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.queue.handlers import readiness_generation
from app.queue.payloads import ReadinessGenerationPayload

SECRET = "SUPER_SECRET_RESUME_TOKEN_42"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "rdy_user") -> UserProfile:
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
) -> ApplicationRecord:
    record = ApplicationRecord(
        user_id=user_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        status="preparing",
        timeline=[],
    )
    db.add(record)
    db.flush()
    return record


def _seed(
    user_id: str = "rdy_user",
    *,
    raw_text: str = "张三\nPython 5年 FastAPI",
    jd_raw: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> dict[str, str]:
    """Seed a user + resume + version + job + application, returning their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id, raw_text)
        job = _make_job(db, user_id, jd_raw=jd_raw)
        app = _make_application(db, user_id, job.id, version.id)
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


def _generate(
    client: TestClient,
    application_id: str,
    artifact_type: str,
    user: str,
) -> Any:
    """POST the generate endpoint and return the response (enqueued, not executed)."""
    return client.post(
        f"/api/v1/applications/{application_id}/artifacts/{artifact_type}/generate",
        headers=_headers(user),
    )


def _fake_enqueue_pool() -> AsyncMock:
    """Return a fake arq pool whose ``enqueue_job`` succeeds without Redis."""
    pool = AsyncMock()
    pool.enqueue_job.return_value = object()
    return pool


def _patch_get_queue_ok() -> Any:
    """Patch ``get_queue`` to return a fake pool (no real Redis needed)."""
    return patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(return_value=_fake_enqueue_pool()),
    )


# ---------------------------------------------------------------------------
# Stub gateways for the worker failure path
# ---------------------------------------------------------------------------


class _InvalidStubGateway(ModelGateway):
    """Gateway that returns malformed JSON to drive the recoverable-failure path."""

    provider_name = "stub"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="not valid json {",
            model="stub-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=0,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


class _RaisingStubGateway(ModelGateway):
    """Gateway whose ``chat()`` raises, to drive the model-call-failure path."""

    provider_name = "stub-raising"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("simulated provider outage")


# ---------------------------------------------------------------------------
# Worker helpers
# ---------------------------------------------------------------------------


def _compute_source_hash_for(
    user_id: str,
    job_id: str,
    resume_version_id: str,
) -> str:
    """Recompute the source hash the way the service does, for payload setup."""
    from app.agents.prompts.readiness import PROMPT_VERSION
    from app.services.application_state import build_source_snapshot

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
            prompt_versions={"readiness": PROMPT_VERSION},
        )
        return snapshot.source_hash


def _create_queued_readiness_run(
    user_id: str,
    application_id: str,
    job_id: str,
    resume_id: str,
    resume_version_id: str,
    artifact_type: str,
    source_hash: str,
    *,
    jd_raw_len: int = 10,
    resume_raw_text_len: int = 10,
) -> str:
    """Insert a queued readiness_generation AgentRun and return its id."""
    with SessionLocal() as db:
        from app.db.repositories import agent_run_repo

        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="readiness_generation",
            status="queued",
            job_id=job_id,
            result={
                "user_id": user_id,
                "application_id": application_id,
                "job_id": job_id,
                "resume_version_id": resume_version_id,
                "resume_id": resume_id,
                "artifact_type": artifact_type,
                "source_hash": source_hash,
                "jd_raw_len": jd_raw_len,
                "resume_raw_text_len": resume_raw_text_len,
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
    artifact_type: str,
    source_hash: str,
) -> ReadinessGenerationPayload:
    return ReadinessGenerationPayload(
        workflow_type="readiness_generation",
        user_id=user_id,
        agent_run_id=run_id,
        idempotency_key=f"readiness_generation:{run_id}",
        application_id=application_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        artifact_type=artifact_type,
        source_hash=source_hash,
    )


# ---------------------------------------------------------------------------
# API layer: successful submit (HTTP 202, queued run, sanitized metadata)
# ---------------------------------------------------------------------------


def test_submit_returns_202_with_queued_run(client: TestClient) -> None:
    ids = _seed("rdy_ok")
    with _patch_get_queue_ok():
        resp = _generate(client, ids["application_id"], "hr_opening_message", "rdy_ok")
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["application_id"] == ids["application_id"]
    assert body["artifact_type"] == "hr_opening_message"
    run = body["run"]
    assert run["status"] == "queued"
    run_id = run["id"]
    assert run_id

    # DB: a queued AgentRun with sanitized metadata (lengths, not raw text).
    with SessionLocal() as db:
        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "queued"
        assert run_row.workflow_type == "readiness_generation"
        assert run_row.user_id == "rdy_ok"
        assert run_row.job_id == ids["job_id"]
        assert run_row.result["application_id"] == ids["application_id"]
        assert run_row.result["artifact_type"] == "hr_opening_message"
        assert run_row.result["source_hash"]
        assert run_row.result["jd_raw_len"] == len(
            "Senior Python backend engineer. Build APIs with FastAPI."
        )
        # Sanitization: no raw text keys in the persisted result.
        assert "jd_raw" not in run_row.result
        assert "raw_text" not in run_row.result


# ---------------------------------------------------------------------------
# API layer: enqueue failure flips run to failed
# ---------------------------------------------------------------------------


def test_submit_enqueue_failure_flips_run_to_failed(client: TestClient) -> None:
    """When Redis is unavailable the endpoint flips the run to ``failed``."""
    ids = _seed("rdy_redis")
    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = _generate(client, ids["application_id"], "interview_prep", "rdy_redis")
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

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["code"] == "queue_enqueue_failed"
        timeline = app.timeline or []
        fail_events = [
            e for e in timeline if e.get("type") == "artifact_generation_failed"
        ]
        assert len(fail_events) == 1
        assert fail_events[0]["metadata"]["error_code"] == "queue_enqueue_failed"


# ---------------------------------------------------------------------------
# API layer: duplicate active-run guard (409)
# ---------------------------------------------------------------------------


def test_submit_duplicate_while_active_returns_409(client: TestClient) -> None:
    """A second submit while a queued run exists for the same artifact type → 409."""
    ids = _seed("rdy_dup")
    with _patch_get_queue_ok():
        first = _generate(client, ids["application_id"], "skill_gap_plan", "rdy_dup")
    assert first.status_code == 202, first.text
    assert first.json()["run"]["status"] == "queued"

    # Second submit while the first run is still queued → 409.
    with _patch_get_queue_ok():
        second = _generate(client, ids["application_id"], "skill_gap_plan", "rdy_dup")
    assert second.status_code == 409, second.text
    assert "already in progress" in second.json()["detail"]


def test_submit_different_artifact_type_allows_concurrent(client: TestClient) -> None:
    """A different artifact type for the same application does NOT 409."""
    ids = _seed("rdy_concurrent")
    with _patch_get_queue_ok():
        first = _generate(client, ids["application_id"], "hr_opening_message", "rdy_concurrent")
    assert first.status_code == 202, first.text

    with _patch_get_queue_ok():
        second = _generate(
            client, ids["application_id"], "interview_prep", "rdy_concurrent"
        )
    assert second.status_code == 202, second.text


def test_submit_after_terminal_allows_regeneration(client: TestClient) -> None:
    """Once the active run reaches a terminal state, re-generation is allowed."""
    ids = _seed("rdy_regen")
    with _patch_get_queue_ok():
        first = _generate(
            client, ids["application_id"], "resume_rewrite_snippet", "rdy_regen"
        )
    assert first.status_code == 202
    run_id = first.json()["run"]["id"]

    # Flip the queued run to a terminal state manually, then submit again.
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.status = "succeeded"
        db.commit()

    with _patch_get_queue_ok():
        second = _generate(
            client, ids["application_id"], "resume_rewrite_snippet", "rdy_regen"
        )
    assert second.status_code == 202, second.text
    assert second.json()["run"]["status"] == "queued"


# ---------------------------------------------------------------------------
# API layer: ownership / data-quality errors (pre-run validation)
# ---------------------------------------------------------------------------


def test_submit_404_for_missing_application(client: TestClient) -> None:
    _seed("rdy_no_app")
    with _patch_get_queue_ok():
        resp = _generate(
            client, "nonexistent_app", "hr_opening_message", "rdy_no_app"
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "application not found"


def test_submit_404_for_cross_user_application(client: TestClient) -> None:
    ids_owner = _seed("rdy_owner_x")
    _seed("rdy_intruder_x")
    with _patch_get_queue_ok():
        resp = _generate(
            client, ids_owner["application_id"], "hr_opening_message", "rdy_intruder_x"
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "application not found"


def test_submit_422_for_invalid_artifact_type(client: TestClient) -> None:
    ids = _seed("rdy_bad_type")
    with _patch_get_queue_ok():
        resp = _generate(
            client, ids["application_id"], "invalid_artifact_type", "rdy_bad_type"
        )
    assert resp.status_code == 422


def test_submit_422_for_empty_raw_text(client: TestClient) -> None:
    """An application whose resume version has no raw text → 422."""
    ids = _seed("rdy_empty_text", raw_text="")
    with _patch_get_queue_ok():
        resp = _generate(
            client, ids["application_id"], "hr_opening_message", "rdy_empty_text"
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "resume version has no parsed text"


def test_submit_422_for_application_without_resume_version(client: TestClient) -> None:
    """An application with no resume version bound → 422."""
    with SessionLocal() as db:
        _make_user(db, "rdy_no_rv")
        resume, version = _make_resume(db, "rdy_no_rv", "张三\nPython")
        job = _make_job(db, "rdy_no_rv")
        record = ApplicationRecord(
            user_id="rdy_no_rv",
            job_id=job.id,
            resume_version_id=None,
            status="planned",
            timeline=[],
        )
        db.add(record)
        db.commit()
        app_id = record.id

    with _patch_get_queue_ok():
        resp = _generate(client, app_id, "hr_opening_message", "rdy_no_rv")
    assert resp.status_code == 422
    assert "resume version" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# API layer: ownership scoping of the persisted run
# ---------------------------------------------------------------------------


def test_submit_run_is_scoped_to_current_user(client: TestClient) -> None:
    ids = _seed("rdy_scope")
    with _patch_get_queue_ok():
        resp = _generate(
            client, ids["application_id"], "interview_prep", "rdy_scope"
        )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.user_id == "rdy_scope"
        assert run.job_id == ids["job_id"]


# ---------------------------------------------------------------------------
# Worker layer: successful generation for all 4 artifact types
# ---------------------------------------------------------------------------

_ARTIFACT_TYPES = [
    "hr_opening_message",
    "resume_rewrite_snippet",
    "skill_gap_plan",
    "interview_prep",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact_type", _ARTIFACT_TYPES)
async def test_handler_success_marks_run_succeeded_and_persists_artifact(
    artifact_type: str,
) -> None:
    """The handler drives the full generation workflow and stores the artifact."""
    # Unique user id per artifact type: parametrized iterations do NOT run the
    # ``client`` fixture (which truncates the DB), so each iteration must seed
    # rows under a fresh key to avoid a duplicate-key violation.
    user_id = f"rdy_ok_{artifact_type}"
    raw_resume = f"张三\nPython 5年 FastAPI。包含密钥 {SECRET}。"
    ids = _seed(user_id, raw_text=raw_resume)
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        user_id,
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        artifact_type,
        source_hash,
        jd_raw_len=50,
        resume_raw_text_len=len(raw_resume),
    )
    payload = _make_payload(
        run_id,
        user_id,
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        artifact_type,
        source_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.workflow_type == "readiness_generation"

        # Six ordered step names, all succeeded.
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        assert [s.name for s in steps] == [
            "load_context",
            "build_prompt_context",
            "call_model",
            "validate_model_output",
            "persist_outputs",
            "complete_run",
        ]
        assert all(s.status == "succeeded" for s in steps)

        # GeneratedArtifact created on success.
        artifact = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert artifact is not None
        assert artifact.artifact_type == artifact_type
        assert artifact.prompt_version == "readiness-v1"
        assert artifact.source_ids["job_id"] == ids["job_id"]
        assert artifact.source_ids["resume_version_id"] == ids["resume_version_id"]
        assert artifact.source_ids["resume_id"] == ids["resume_id"]
        assert artifact.source_ids["user_id"] == user_id
        assert artifact.source_ids["application_id"] == ids["application_id"]
        assert artifact.source_ids["source_hash"] == source_hash
        assert artifact.source_ids["model_request_id"]
        assert artifact.source_ids["provider"]

        # The artifact content is valid JSON matching the expected fake output.
        content = json.loads(artifact.content)
        assert isinstance(content, dict)

        # AgentRun.result carries source-context + output IDs.
        assert run.result["application_id"] == ids["application_id"]
        assert run.result["artifact_id"] == artifact.id
        assert run.result["source_hash"] == source_hash
        assert "source_context" in run.result
        assert "truncation" in run.result["source_context"]

        # Timeline event appended to the application record.
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        timeline = app.timeline or []
        gen_events = [e for e in timeline if e.get("type") == "artifact_generated"]
        assert len(gen_events) == 1
        assert gen_events[0]["metadata"]["artifact_type"] == artifact_type
        assert gen_events[0]["metadata"]["agent_run_id"] == run_id

        # readiness_snapshot updated with source_hash + artifact_id.
        assert app.readiness_snapshot is not None
        assert app.readiness_snapshot["source_hash"] == source_hash
        assert app.readiness_snapshot["artifact_type"] == artifact_type

        # Sanitization: the secret token must not appear in any step result,
        # run result/error, or artifact content/source_ids.
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob
        assert SECRET not in artifact.content
        assert SECRET not in json.dumps(artifact.source_ids, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Worker layer: dict payload (arq delivers deserialized dicts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_accepts_dict_payload() -> None:
    """arq delivers a deserialized dict; the handler must accept it."""
    ids = _seed("rdy_worker_dict")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_worker_dict",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )
    payload_dict = {
        "workflow_type": "readiness_generation",
        "user_id": "rdy_worker_dict",
        "agent_run_id": run_id,
        "idempotency_key": f"readiness_generation:{run_id}",
        "application_id": ids["application_id"],
        "job_id": ids["job_id"],
        "resume_version_id": ids["resume_version_id"],
        "artifact_type": "interview_prep",
        "source_hash": source_hash,
    }

    result = await readiness_generation({"job_id": "arq-job-1"}, payload_dict)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"


# ---------------------------------------------------------------------------
# Worker layer: skip terminal run (no duplicate outputs on retry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_skips_terminal_run_without_duplicate_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retried queue job for a terminal run must not create duplicate outputs."""
    ids = _seed("rdy_worker_retry")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_worker_retry",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "skill_gap_plan",
        source_hash,
    )
    payload = _make_payload(
        run_id,
        "rdy_worker_retry",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "skill_gap_plan",
        source_hash,
    )

    result = await readiness_generation({}, payload)
    assert result == run_id

    # If the handler does not short-circuit terminal runs, this second delivery
    # would call the raising gateway and flip the already-succeeded run failed
    # or create duplicate rows.
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    retry_result = await readiness_generation({}, payload)
    assert retry_result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"

        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        assert len(artifacts) == 1
        assert len(steps) == 6


# ---------------------------------------------------------------------------
# Worker layer: recoverable failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_failure_invalid_json_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model returns invalid JSON → run failed, validate_model_output step failed."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _InvalidStubGateway(),
    )
    ids = _seed("rdy_worker_inv")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_worker_inv",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "hr_opening_message",
        source_hash,
    )
    payload = _make_payload(
        run_id,
        "rdy_worker_inv",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "hr_opening_message",
        source_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "model returned invalid output"

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "validate_model_output"

        # Failure path must NOT create a GeneratedArtifact.
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []

        # A failure envelope + timeline event must be persisted.
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["code"] == "invalid_model_output"
        timeline = app.timeline or []
        fail_events = [
            e for e in timeline if e.get("type") == "artifact_generation_failed"
        ]
        assert len(fail_events) == 1


@pytest.mark.asyncio
async def test_handler_failure_raising_gateway_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway raises → run failed, call_model step failed."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    ids = _seed("rdy_worker_raise")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_worker_raise",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )
    payload = _make_payload(
        run_id,
        "rdy_worker_raise",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "model call failed"

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "call_model"
        assert failed[0].result["provider"] == "stub-raising"
        assert "error_type" in failed[0].result

        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []


# ---------------------------------------------------------------------------
# Worker layer: stale source detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_stale_source_marks_run_failed() -> None:
    """When the source hash changes between enqueue and execution, the run fails
    with a ``stale_source`` code and no artifact is persisted.
    """
    ids = _seed("rdy_stale")
    # Pass a deliberately wrong source hash so it never matches the current one.
    wrong_hash = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    run_id = _create_queued_readiness_run(
        "rdy_stale",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "skill_gap_plan",
        wrong_hash,
    )
    payload = _make_payload(
        run_id,
        "rdy_stale",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "skill_gap_plan",
        wrong_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "stale source detected"

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "load_context"

        # No artifact created.
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []

        # Failure envelope persisted with stale_source code.
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["code"] == "stale_source"
        timeline = app.timeline or []
        fail_events = [
            e for e in timeline if e.get("type") == "artifact_generation_failed"
        ]
        assert len(fail_events) == 1
        assert fail_events[0]["metadata"]["error_code"] == "stale_source"


@pytest.mark.asyncio
async def test_handler_empty_resume_text_marks_application_failure() -> None:
    """If the resume text disappears after enqueue, the failed run is visible on
    the application record and no artifact is persisted.
    """
    ids = _seed("rdy_worker_empty_late")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_worker_empty_late",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )
    with SessionLocal() as db:
        version = db.get(ResumeVersion, ids["resume_version_id"])
        assert version is not None
        version.raw_text = "   "
        db.commit()

    payload = _make_payload(
        run_id,
        "rdy_worker_empty_late",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "resume version has no parsed text"

        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["code"] == "resume_text_missing"
        timeline = app.timeline or []
        fail_events = [
            e for e in timeline if e.get("type") == "artifact_generation_failed"
        ]
        assert len(fail_events) == 1
        assert fail_events[0]["metadata"]["error_code"] == "resume_text_missing"


# ---------------------------------------------------------------------------
# Worker layer: ownership mismatch + missing run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_owner_mismatch_fails_run() -> None:
    """A cross-user payload must not execute; the run is failed with a sanitized error."""
    ids = _seed("rdy_real_owner")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_real_owner",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "hr_opening_message",
        source_hash,
    )
    # Payload claims a different user.
    payload = _make_payload(
        run_id,
        "rdy_attacker",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "hr_opening_message",
        source_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == "ownership_mismatch"
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "ownership mismatch"


@pytest.mark.asyncio
async def test_handler_missing_run_returns_marker() -> None:
    """A missing run id degrades gracefully instead of raising."""
    ids = _seed("rdy_ghost")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    payload = _make_payload(
        "does_not_exist",
        "rdy_ghost",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "hr_opening_message",
        source_hash,
    )
    result = await readiness_generation({}, payload)
    assert result == "missing_run"


# ---------------------------------------------------------------------------
# Worker layer: sanitization (no raw resume content leakage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_sanitizes_raw_resume_from_all_persisted_rows() -> None:
    raw_resume = f"简历内容，机密字段 {SECRET} 请勿泄露。"
    ids = _seed("rdy_worker_san", raw_text=raw_resume)
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_worker_san",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "resume_rewrite_snippet",
        source_hash,
        resume_raw_text_len=len(raw_resume),
    )
    payload = _make_payload(
        run_id,
        "rdy_worker_san",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "resume_rewrite_snippet",
        source_hash,
    )

    await readiness_generation({}, payload)

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob

        artifact = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert artifact is not None
        assert SECRET not in artifact.content
        assert SECRET not in json.dumps(artifact.source_ids, ensure_ascii=False)

        # The timeline event metadata must not leak the secret either.
        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        timeline_blob = json.dumps(app.timeline or [], ensure_ascii=False)
        assert SECRET not in timeline_blob


# ---------------------------------------------------------------------------
# Worker layer: failed run is visible via agent-runs detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_run_visible_via_agent_runs_detail(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """A failed run is still findable via ``GET /agent-runs/{id}/detail``."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    ids = _seed("rdy_visibility")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_visibility",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )
    payload = _make_payload(
        run_id,
        "rdy_visibility",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "interview_prep",
        source_hash,
    )

    await readiness_generation({}, payload)

    # GET /agent-runs/{run_id}/detail shows the failed call_model step.
    detail_resp = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("rdy_visibility"),
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["id"] == run_id
    assert detail["status"] == "failed"
    call_step = next(s for s in detail["steps"] if s["name"] == "call_model")
    assert call_step["status"] == "failed"
    assert call_step["result"]["provider"] == "stub-raising"
    assert "error_type" in call_step["result"]

    # Cross-user access is 404 (not 403).
    intruder = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("rdy_visibility_intruder"),
    )
    assert intruder.status_code == 404


# ---------------------------------------------------------------------------
# Worker layer: successful run detail with ordered steps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_detail_returns_ordered_steps_user_scoped(
    client: TestClient,
) -> None:
    """Run detail with ordered steps, scoped to the current user."""
    ids = _seed("rdy_detail")
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        "rdy_detail",
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "skill_gap_plan",
        source_hash,
    )
    payload = _make_payload(
        run_id,
        "rdy_detail",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "skill_gap_plan",
        source_hash,
    )
    await readiness_generation({}, payload)

    # Owner sees the full step trail.
    owner = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("rdy_detail"),
    )
    assert owner.status_code == 200, owner.text
    detail = owner.json()
    assert detail["id"] == run_id
    assert detail["status"] == "succeeded"
    step_names = [s["name"] for s in detail["steps"]]
    assert step_names == [
        "load_context",
        "build_prompt_context",
        "call_model",
        "validate_model_output",
        "persist_outputs",
        "complete_run",
    ]
    assert all(s["status"] == "succeeded" for s in detail["steps"])
    assert [s["step_no"] for s in detail["steps"]] == [1, 2, 3, 4, 5, 6]

    # Cross-user access is 404 (not 403).
    intruder = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("rdy_detail_intruder"),
    )
    assert intruder.status_code == 404
