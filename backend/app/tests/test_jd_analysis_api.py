"""API + worker integration tests for the resume-aware JD analysis workflow.

The analysis endpoint now uses the enqueue-and-poll pattern (design.md):

- ``POST /jobs/{job_id}/analyses`` validates ownership + resume-version
  usability up front (404/422 bubble before any run is created), then creates a
  ``queued`` ``AgentRun`` (``workflow_type="resume_aware_jd_analysis"``),
  enqueues a ``ResumeAwareJdAnalysisPayload`` to the worker, and returns
  immediately with HTTP 202 + ``RunJdAnalysisSubmitResponse`` (run summary +
  echoed resume_version_id). It does **not** block on the model call.
- The worker handler ``resume_aware_jd_analysis`` executes the analysis
  workflow (``run_resume_aware_jd_analysis_worker``): flips the run queued →
  running → succeeded/failed, persists six sanitized ``AgentStep`` rows, and on
  success stores ``JobAnalysis`` + ``GeneratedArtifact`` so the frontend can
  hydrate them by polling ``GET /agent-runs/{id}/detail`` or listing analyses.

Tests cover both layers:

API layer (HTTP 202 contract):

- successful submit: 202, ``run.status == "queued"``, resume_version_id echoed,
  an ``AgentRun`` row is persisted in ``queued`` state with sanitized result
  (jd_raw_len / resume_raw_text_len, not raw text).
- enqueue failure (Redis down): the run is flipped to ``failed`` before
  returning so the frontend never polls forever.
- duplicate submit while a queued/running run exists → 409.
- 404 for missing/cross-user job; 404 for missing/cross-user resume version;
  422 for a resume version with no raw text.

Worker layer (handler execution):

- successful analysis via the ``resume_aware_jd_analysis`` handler: run
  succeeds, six ordered steps persisted, ``JobAnalysis`` +
  ``GeneratedArtifact`` created, artifact source_ids include job_id +
  resume_version_id, sanitization (no raw resume token in any persisted row).
- model-invalid output (stub gateway) → run failed, validate_model_output step
  failed, no JobAnalysis / GeneratedArtifact created.
- gateway raises → run failed, call_model step failed.
- ownership mismatch → run failed with sanitized error.
- missing run → handler returns ``"missing_run"`` marker.

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
    GeneratedArtifact,
    JobAnalysis,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.queue.handlers import resume_aware_jd_analysis
from app.queue.payloads import ResumeAwareJdAnalysisPayload

SECRET = "SUPER_SECRET_RESUME_TOKEN_42"

# ---------------------------------------------------------------------------
# DB helpers (mirror test_jd_analysis_contracts.py so fixtures stay independent)
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "phase4_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="Phase4 用户")
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


def _seed(
    user_id: str = "phase4_user",
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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _submit(client: TestClient, job_id: str, version_id: str, user: str) -> Any:
    """POST /jobs/{job_id}/analyses and return the response (enqueued, not executed)."""
    return client.post(
        f"/api/v1/jobs/{job_id}/analyses",
        json={"resume_version_id": version_id},
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


def _create_queued_analysis_run(
    user_id: str,
    job_id: str,
    resume_version_id: str,
    *,
    jd_raw_len: int = 10,
    resume_raw_text_len: int = 10,
) -> str:
    """Insert a queued resume_aware_jd_analysis AgentRun and return its id.

    Mirrors what the API endpoint does: creates the run with sanitized metadata
    *before* the worker picks it up.
    """
    with SessionLocal() as db:
        from app.db.repositories import agent_run_repo

        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="resume_aware_jd_analysis",
            status="queued",
            job_id=job_id,
            result={
                "user_id": user_id,
                "job_id": job_id,
                "resume_version_id": resume_version_id,
                "jd_raw_len": jd_raw_len,
                "resume_raw_text_len": resume_raw_text_len,
            },
        )
        db.commit()
        return run.id


def _make_payload(
    run_id: str,
    user_id: str,
    job_id: str,
    resume_version_id: str,
) -> ResumeAwareJdAnalysisPayload:
    return ResumeAwareJdAnalysisPayload(
        workflow_type="resume_aware_jd_analysis",
        user_id=user_id,
        agent_run_id=run_id,
        idempotency_key=f"jd_analysis:{run_id}",
        job_id=job_id,
        resume_version_id=resume_version_id,
    )


# ---------------------------------------------------------------------------
# API layer: successful submit (HTTP 202, queued run, sanitized metadata)
# ---------------------------------------------------------------------------


def test_submit_returns_202_with_queued_run(client: TestClient) -> None:
    ids = _seed("ja_ok")
    with _patch_get_queue_ok():
        resp = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_ok")
    assert resp.status_code == 202, resp.text
    body = resp.json()

    # The submit response echoes resume_version_id and carries the run summary.
    assert body["resume_version_id"] == ids["resume_version_id"]
    run = body["run"]
    assert run["status"] == "queued"
    run_id = run["id"]
    assert run_id

    # DB: a queued AgentRun with sanitized metadata (lengths, not raw text).
    with SessionLocal() as db:
        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "queued"
        assert run_row.workflow_type == "resume_aware_jd_analysis"
        assert run_row.user_id == "ja_ok"
        assert run_row.job_id == ids["job_id"]
        assert run_row.result["job_id"] == ids["job_id"]
        assert run_row.result["resume_version_id"] == ids["resume_version_id"]
        assert run_row.result["jd_raw_len"] == len(
            "Senior Python backend engineer. Build APIs with FastAPI."
        )
        # Sanitization: the secret must not appear in the persisted result.
        run_blob = json.dumps(run_row.result or {}, ensure_ascii=False) + (run_row.error or "")
        assert SECRET not in run_blob


# ---------------------------------------------------------------------------
# API layer: enqueue failure flips run to failed
# ---------------------------------------------------------------------------


def test_submit_enqueue_failure_flips_run_to_failed(client: TestClient) -> None:
    """When Redis is unavailable the endpoint flips the run to ``failed``."""
    ids = _seed("ja_redis")
    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_redis")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # The run is returned as failed so the frontend sees a terminal state.
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


def test_submit_duplicate_while_active_returns_409(client: TestClient) -> None:
    """A second submit while a queued/running run exists returns 409."""
    ids = _seed("ja_dup")
    with _patch_get_queue_ok():
        first = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_dup")
    assert first.status_code == 202, first.text
    assert first.json()["run"]["status"] == "queued"

    # Second submit while the first run is still queued → 409.
    with _patch_get_queue_ok():
        second = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_dup")
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "analysis already in progress for this job"

    # Still only one run for this job.
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(AgentRun).where(
                    AgentRun.job_id == ids["job_id"],
                    AgentRun.workflow_type == "resume_aware_jd_analysis",
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


def test_submit_after_terminal_allows_reanalysis(client: TestClient) -> None:
    """Once the active run reaches a terminal state, re-analysis is allowed."""
    ids = _seed("ja_reanalyze")
    with _patch_get_queue_ok():
        first = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_reanalyze")
    assert first.status_code == 202
    run_id = first.json()["run"]["id"]

    # Flip the queued run to a terminal state manually (simulating the worker
    # finishing), then submit again — it should succeed, not 409.
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.status = "succeeded"
        db.commit()

    with _patch_get_queue_ok():
        second = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_reanalyze")
    assert second.status_code == 202, second.text
    assert second.json()["run"]["status"] == "queued"


# ---------------------------------------------------------------------------
# API layer: ownership / data-quality errors (pre-run validation)
# ---------------------------------------------------------------------------


def test_submit_404_for_missing_job(client: TestClient) -> None:
    ids = _seed("ja_no_job")
    resp = _submit(client, "nonexistent_job", ids["resume_version_id"], "ja_no_job")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"


def test_submit_404_for_cross_user_job(client: TestClient) -> None:
    ids_owner = _seed("ja_owner_x")
    ids_intruder = _seed("ja_intruder_x")
    resp = _submit(
        client,
        ids_owner["job_id"],
        ids_intruder["resume_version_id"],
        "ja_intruder_x",
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"


def test_submit_404_for_missing_resume_version(client: TestClient) -> None:
    ids = _seed("ja_no_ver")
    resp = _submit(client, ids["job_id"], "nonexistent_ver", "ja_no_ver")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume version not found"


def test_submit_404_for_cross_user_resume_version(client: TestClient) -> None:
    ids_owner = _seed("ja_rv_owner")
    ids_intruder = _seed("ja_rv_intruder")
    resp = _submit(
        client,
        ids_intruder["job_id"],
        ids_owner["resume_version_id"],
        "ja_rv_intruder",
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume version not found"


def test_submit_422_for_empty_raw_text(client: TestClient) -> None:
    ids = _seed("ja_empty_text", raw_text="")
    resp = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_empty_text")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "resume version has no parsed text"


# ---------------------------------------------------------------------------
# API layer: ownership scoping of the persisted run
# ---------------------------------------------------------------------------


def test_submit_run_is_scoped_to_current_user(client: TestClient) -> None:
    ids = _seed("ja_scope")
    with _patch_get_queue_ok():
        resp = _submit(client, ids["job_id"], ids["resume_version_id"], "ja_scope")
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.user_id == "ja_scope"
        assert run.job_id == ids["job_id"]


# ---------------------------------------------------------------------------
# Worker layer: successful analysis via resume_aware_jd_analysis handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_success_marks_run_succeeded_and_persists_outputs() -> None:
    """The handler drives the full analysis workflow and stores analysis+artifact."""
    raw_resume = f"张三\nPython 5年 FastAPI。包含密钥 {SECRET}。"
    ids = _seed("ja_worker_ok", raw_text=raw_resume)
    run_id = _create_queued_analysis_run(
        "ja_worker_ok",
        ids["job_id"],
        ids["resume_version_id"],
        jd_raw_len=50,
        resume_raw_text_len=len(raw_resume),
    )
    payload = _make_payload(run_id, "ja_worker_ok", ids["job_id"], ids["resume_version_id"])

    result = await resume_aware_jd_analysis({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.workflow_type == "resume_aware_jd_analysis"

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

        # JobAnalysis + GeneratedArtifact created on success.
        analysis = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert analysis is not None
        assert analysis.job_id == ids["job_id"]
        assert isinstance(analysis.match_score, float)
        assert isinstance(analysis.risk_score, float)
        assert analysis.summary

        artifact = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert artifact is not None
        assert artifact.artifact_type == "jd_analysis"
        assert artifact.prompt_version == "jd-analysis-v2"
        assert artifact.source_ids["job_id"] == ids["job_id"]
        assert artifact.source_ids["resume_version_id"] == ids["resume_version_id"]
        assert artifact.source_ids["resume_id"] == ids["resume_id"]
        assert artifact.source_ids["user_id"] == "ja_worker_ok"
        assert artifact.source_ids["model_request_id"]
        assert artifact.source_ids["provider"]

        # AgentRun.result carries source-context + output IDs.
        assert run.result["job_id"] == ids["job_id"]
        assert run.result["resume_version_id"] == ids["resume_version_id"]
        assert run.result["analysis_id"] == analysis.id
        assert run.result["artifact_id"] == artifact.id
        assert "source_context" in run.result
        assert "truncation" in run.result["source_context"]

        # Sanitization: the secret token must not appear in any step result,
        # run result/error, or artifact content/source_ids.
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob
        assert SECRET not in artifact.content
        assert SECRET not in json.dumps(artifact.source_ids, ensure_ascii=False)


@pytest.mark.asyncio
async def test_handler_skips_terminal_run_without_duplicate_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retried queue job for a terminal run must not create duplicate outputs."""
    ids = _seed("ja_worker_retry")
    run_id = _create_queued_analysis_run(
        "ja_worker_retry",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload = _make_payload(run_id, "ja_worker_retry", ids["job_id"], ids["resume_version_id"])

    result = await resume_aware_jd_analysis({}, payload)
    assert result == run_id

    # If the handler does not short-circuit terminal runs, this second delivery
    # would call the raising gateway and flip the already-succeeded run failed
    # or create duplicate rows.
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    retry_result = await resume_aware_jd_analysis({}, payload)
    assert retry_result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"

        analyses = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .all()
        )
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        assert len(analyses) == 1
        assert len(artifacts) == 1
        assert len(steps) == 6


@pytest.mark.asyncio
async def test_handler_accepts_dict_payload() -> None:
    """arq delivers a deserialized dict; the handler must accept it."""
    ids = _seed("ja_worker_dict")
    run_id = _create_queued_analysis_run(
        "ja_worker_dict",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload_dict = {
        "workflow_type": "resume_aware_jd_analysis",
        "user_id": "ja_worker_dict",
        "agent_run_id": run_id,
        "idempotency_key": f"jd_analysis:{run_id}",
        "job_id": ids["job_id"],
        "resume_version_id": ids["resume_version_id"],
    }

    result = await resume_aware_jd_analysis({"job_id": "arq-job-1"}, payload_dict)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"


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
    ids = _seed("ja_worker_inv")
    run_id = _create_queued_analysis_run(
        "ja_worker_inv",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload = _make_payload(run_id, "ja_worker_inv", ids["job_id"], ids["resume_version_id"])

    result = await resume_aware_jd_analysis({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "model returned invalid analysis"

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "validate_model_output"

        # Failure path must NOT create JobAnalysis / GeneratedArtifact.
        analyses = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert analyses == []
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []


@pytest.mark.asyncio
async def test_handler_failure_raising_gateway_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway raises → run failed, call_model step failed."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    ids = _seed("ja_worker_raise")
    run_id = _create_queued_analysis_run(
        "ja_worker_raise",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload = _make_payload(run_id, "ja_worker_raise", ids["job_id"], ids["resume_version_id"])

    result = await resume_aware_jd_analysis({}, payload)

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

        analyses = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert analyses == []
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []


# ---------------------------------------------------------------------------
# Worker layer: ownership mismatch + missing run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_owner_mismatch_fails_run() -> None:
    """A cross-user payload must not execute; the run is failed with a sanitized error."""
    ids = _seed("ja_real_owner")
    run_id = _create_queued_analysis_run(
        "ja_real_owner",
        ids["job_id"],
        ids["resume_version_id"],
    )
    # Payload claims a different user.
    payload = _make_payload(run_id, "ja_attacker", ids["job_id"], ids["resume_version_id"])

    result = await resume_aware_jd_analysis({}, payload)

    assert result == "ownership_mismatch"
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "ownership mismatch"


@pytest.mark.asyncio
async def test_handler_missing_run_returns_marker() -> None:
    """A missing run id degrades gracefully instead of raising."""
    ids = _seed("ja_ghost")
    payload = _make_payload("does_not_exist", "ja_ghost", ids["job_id"], ids["resume_version_id"])
    result = await resume_aware_jd_analysis({}, payload)
    assert result == "missing_run"


# ---------------------------------------------------------------------------
# Worker layer: failed run is visible via agent-runs + run detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_run_visible_via_agent_runs_and_detail(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """R4: a failed run that created no JobAnalysis is still findable via
    ``GET /agent-runs?job_id=…`` and its failed step metadata is readable via
    ``GET /agent-runs/{id}/detail``.
    """
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    ids = _seed("ja_visibility")
    run_id = _create_queued_analysis_run(
        "ja_visibility",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload = _make_payload(run_id, "ja_visibility", ids["job_id"], ids["resume_version_id"])

    await resume_aware_jd_analysis({}, payload)

    # GET /jobs/{job_id}/analyses is still empty (no JobAnalysis was created).
    analyses_resp = client.get(
        f"/api/v1/jobs/{ids['job_id']}/analyses",
        headers=_headers("ja_visibility"),
    )
    assert analyses_resp.status_code == 200, analyses_resp.text
    assert analyses_resp.json()["meta"]["total"] == 0

    # But GET /agent-runs?job_id=…&workflow_type=resume_aware_jd_analysis
    # returns the failed run.
    runs_resp = client.get(
        "/api/v1/agent-runs",
        params={
            "job_id": ids["job_id"],
            "workflow_type": "resume_aware_jd_analysis",
        },
        headers=_headers("ja_visibility"),
    )
    assert runs_resp.status_code == 200, runs_resp.text
    runs_body = runs_resp.json()
    assert runs_body["meta"]["total"] == 1
    run_summary = runs_body["items"][0]
    assert run_summary["status"] == "failed"
    assert run_summary["created_at"] is not None

    # GET /agent-runs/{run_id}/detail shows the failed call_model step and its
    # sanitized metadata.
    detail_resp = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("ja_visibility"),
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["created_at"] is not None
    call_step = next(s for s in detail["steps"] if s["name"] == "call_model")
    assert call_step["status"] == "failed"
    assert call_step["result"]["provider"] == "stub-raising"
    assert "error_type" in call_step["result"]
    assert call_step["created_at"] is not None


# ---------------------------------------------------------------------------
# Worker layer: sanitization (no raw resume content leakage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_sanitizes_raw_resume_from_all_persisted_rows() -> None:
    raw_resume = f"简历内容，机密字段 {SECRET} 请勿泄露。"
    ids = _seed("ja_worker_san", raw_text=raw_resume)
    run_id = _create_queued_analysis_run(
        "ja_worker_san",
        ids["job_id"],
        ids["resume_version_id"],
        resume_raw_text_len=len(raw_resume),
    )
    payload = _make_payload(run_id, "ja_worker_san", ids["job_id"], ids["resume_version_id"])

    await resume_aware_jd_analysis({}, payload)

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


# ---------------------------------------------------------------------------
# GET list + run detail (post-worker-success hydration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_analyses_returns_persisted_result_for_hydration(
    client: TestClient,
) -> None:
    """R1/R2: a persisted analysis round-trips through the list endpoint with
    enough data (analysis + artifact + structured) to reconstruct the UI.
    """
    ids = _seed("ja_hydrate")
    run_id = _create_queued_analysis_run(
        "ja_hydrate",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload = _make_payload(run_id, "ja_hydrate", ids["job_id"], ids["resume_version_id"])
    await resume_aware_jd_analysis({}, payload)

    # Fresh read — do not rely on any in-memory state.
    resp = client.get(
        f"/api/v1/jobs/{ids['job_id']}/analyses",
        headers=_headers("ja_hydrate"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["total"] == 1
    item = body["items"][0]
    # Analysis row carried through.
    assert item["analysis"]["agent_run_id"] == run_id
    assert item["analysis"]["job_id"] == ids["job_id"]
    # Artifact metadata carried through.
    assert item["artifact"] is not None
    assert item["artifact"]["artifact_type"] == "jd_analysis"
    assert item["artifact"]["agent_run_id"] == run_id
    # Structured output re-parsed from artifact.content.
    assert item["structured"] is not None
    assert item["structured"]["match_score"] is not None


@pytest.mark.asyncio
async def test_run_detail_returns_ordered_steps_user_scoped(
    client: TestClient,
) -> None:
    """R3/R5: run detail with ordered steps, scoped to the current user."""
    ids = _seed("ja_detail")
    run_id = _create_queued_analysis_run(
        "ja_detail",
        ids["job_id"],
        ids["resume_version_id"],
    )
    payload = _make_payload(run_id, "ja_detail", ids["job_id"], ids["resume_version_id"])
    await resume_aware_jd_analysis({}, payload)

    # Owner sees the full step trail.
    owner = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("ja_detail"),
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
    # Steps are ordered by step_no.
    assert [s["step_no"] for s in detail["steps"]] == [1, 2, 3, 4, 5, 6]
    assert detail["created_at"] is not None
    assert all(s["created_at"] is not None for s in detail["steps"])

    # Cross-user access is 404 (not 403) — R5.
    intruder = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("ja_detail_intruder"),
    )
    assert intruder.status_code == 404


def test_list_analyses_404_for_cross_user_job(client: TestClient) -> None:
    ids_owner = _seed("ja_list_owner")
    _seed("ja_list_intruder")
    resp = client.get(
        f"/api/v1/jobs/{ids_owner['job_id']}/analyses",
        headers=_headers("ja_list_intruder"),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"


# ---------------------------------------------------------------------------
# Worker layer: H2 stale-source detection (design.md §H2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_source_fails_run_without_model_call_or_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the source hash changed between enqueue and worker execution the
    run fails with ``code = "stale_source"`` *before* the model is called, and
    no ``JobAnalysis`` / ``GeneratedArtifact`` is persisted.
    """
    # A gateway that would raise if the worker reached the model call. The
    # stale-source guard must short-circuit before this point.
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    ids = _seed("ja_stale")
    run_id = _create_queued_analysis_run(
        "ja_stale",
        ids["job_id"],
        ids["resume_version_id"],
    )

    # Capture the enqueue-time source hash from the freshly-seeded rows WITHOUT
    # mutating them. Then mutate the job's updated_at so the worker's
    # recomputed hash differs. The source hash covers job_id / job_updated_at /
    # resume_version_id / resume_version_no / profile_updated_at / prompt
    # versions (design.md §H2), so changing job_updated_at is sufficient to
    # produce a stale mismatch.
    from app.db.models.models import ResumeVersion as _RV
    from app.db.models.models import UserProfile as _UP
    from app.services.application_state import build_source_snapshot

    with SessionLocal() as db:
        job = db.get(JobPosting, ids["job_id"])
        version = db.get(_RV, ids["resume_version_id"])
        user = db.get(_UP, "ja_stale")
        assert job is not None and version is not None and user is not None
        snapshot = build_source_snapshot(
            job_id=job.id,
            job_updated_at=job.updated_at,
            resume_version_id=version.id,
            resume_version_no=version.version_no,
            profile_updated_at=user.updated_at,
            prompt_versions={"jd_analysis": "jd-analysis-v2"},
        )
        stale_hash = snapshot.source_hash

        # Mutate job.updated_at directly to a different timestamp so the
        # worker recomputes a different hash. We bypass onupdate by using a
        # raw UPDATE so the column reflects exactly the value we set.
        from datetime import timedelta
        drifted = job.updated_at + timedelta(hours=1)
        db.execute(
            JobPosting.__table__.update()
            .where(JobPosting.__table__.c.id == job.id)
            .values(updated_at=drifted)
        )
        db.commit()

    payload = ResumeAwareJdAnalysisPayload(
        workflow_type="resume_aware_jd_analysis",
        user_id="ja_stale",
        agent_run_id=run_id,
        idempotency_key=f"jd_analysis:{run_id}",
        job_id=ids["job_id"],
        resume_version_id=ids["resume_version_id"],
        source_hash=stale_hash,
    )

    result = await resume_aware_jd_analysis({}, payload)
    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "stale source detected"

        # The stale-source step is the only persisted step and it is failed.
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        assert len(steps) == 1
        assert steps[0].name == "stale_source_check"
        assert steps[0].status == "failed"
        assert steps[0].result["enqueue_source_hash"] == stale_hash
        assert steps[0].result["current_source_hash"] != stale_hash

        # latest_error carries the sanitized failure envelope with the stale
        # source code and a retry next-action.
        assert run.result is not None
        assert "failure" in run.result
        failure = run.result["failure"]
        assert failure["code"] == "stale_source"
        assert failure["category"] == "data"
        assert failure["retryable"] is True
        assert failure["next_action"] == "retry"
        assert failure["agent_run_id"] == run_id
        assert failure["source_ids"]["job_id"] == ids["job_id"]
        assert failure["source_ids"]["resume_version_id"] == ids["resume_version_id"]

        # No analysis or artifact may be persisted on the stale path.
        analyses = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert analyses == []
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .all()
        )
        assert artifacts == []


@pytest.mark.asyncio
async def test_fresh_source_proceeds_to_model_call_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the enqueue-time source hash still matches at worker execution the
    run proceeds normally to the model call and succeeds (no false stale
    rejection). The fake-model gateway returns a valid analysis.
    """
    from app.models_gateway.factory import get_model_gateway

    ids = _seed("ja_fresh")
    run_id = _create_queued_analysis_run(
        "ja_fresh",
        ids["job_id"],
        ids["resume_version_id"],
    )

    from app.db.models.models import UserProfile as _UP
    from app.services.jd_analysis_service import compute_enqueue_source_hash

    with SessionLocal() as db:
        user = db.get(_UP, "ja_fresh")
        assert user is not None
        fresh_hash = compute_enqueue_source_hash(
            db=db,
            current_user=user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
        )

    payload = ResumeAwareJdAnalysisPayload(
        workflow_type="resume_aware_jd_analysis",
        user_id="ja_fresh",
        agent_run_id=run_id,
        idempotency_key=f"jd_analysis:{run_id}",
        job_id=ids["job_id"],
        resume_version_id=ids["resume_version_id"],
        source_hash=fresh_hash,
    )

    # Use the real fake gateway (MODEL_PROVIDER=fake) so the analysis succeeds.
    _ = get_model_gateway  # ensure import path is valid
    result = await resume_aware_jd_analysis({}, payload)
    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        # No stale-source step on the fresh path.
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        assert all(s.name != "stale_source_check" for s in steps)
        # The analysis + artifact were created.
        analysis = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert analysis is not None
        artifact = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert artifact is not None


@pytest.mark.asyncio
async def test_no_source_hash_skips_stale_check_and_succeeds() -> None:
    """When ``source_hash`` is ``None`` (back-compat / older payloads) the
    worker must skip the stale check and proceed normally. This protects
    in-flight payloads enqueued before the H2 field was added.
    """
    ids = _seed("ja_no_hash")
    run_id = _create_queued_analysis_run(
        "ja_no_hash",
        ids["job_id"],
        ids["resume_version_id"],
    )
    # payload.source_hash defaults to None.
    payload = _make_payload(run_id, "ja_no_hash", ids["job_id"], ids["resume_version_id"])
    assert payload.source_hash is None

    result = await resume_aware_jd_analysis({}, payload)
    assert result == run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        assert all(s.name != "stale_source_check" for s in steps)
