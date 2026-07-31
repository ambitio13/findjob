"""Phase 4 orchestration + API tests for the resume-aware JD analysis workflow.

Covers the persistence + API integration layer (design.md §5, §9, §11):

- successful run via the API: 201, response carries agent_run(succeeded) +
  analysis + artifact + structured; DB has AgentRun + AgentStep(s) +
  JobAnalysis + GeneratedArtifact; artifact source_ids includes job_id and
  resume_version_id.
- failed model validation: a stub gateway returning invalid JSON → 502, a
  FAILED AgentRun + failed AgentStep persisted, no JobAnalysis /
  GeneratedArtifact created.
- 404 for missing/cross-user job; 404 for missing/cross-user resume version;
  422 for a resume version with no raw text.
- GET list pagination + user scoping (cross-user job is 404; only the owner's
  analyses come back).

Tests use the shared ``client`` fixture (truncated test DB, fake provider).
The stub gateway for the failure path is wired in via FastAPI dependency
overrides, matching how the existing suite overrides nothing else.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_model_gateway_dep
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
from app.main import app
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway

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


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _run(client: TestClient, job_id: str, version_id: str, user: str) -> Any:
    return client.post(
        f"/api/v1/jobs/{job_id}/analyses",
        json={"resume_version_id": version_id},
        headers=_headers(user),
    )


# ---------------------------------------------------------------------------
# Stub gateway for the model-validation-failure path
# ---------------------------------------------------------------------------


class _InvalidStubGateway(ModelGateway):
    """Gateway that returns malformed JSON to drive the 502 path."""

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


@pytest.fixture()
def invalid_gateway_override():
    """Override ``get_model_gateway_dep`` with the invalid-output stub."""
    stub = _InvalidStubGateway()
    app.dependency_overrides[get_model_gateway_dep] = lambda: stub
    try:
        yield stub
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)


class _RaisingStubGateway(ModelGateway):
    """Gateway whose ``chat()`` raises, to drive the model-call-failure 502 path."""

    provider_name = "stub-raising"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("simulated provider outage")


@pytest.fixture()
def raising_gateway_override():
    """Override ``get_model_gateway_dep`` with the raising stub."""
    stub = _RaisingStubGateway()
    app.dependency_overrides[get_model_gateway_dep] = lambda: stub
    try:
        yield stub
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)


# ---------------------------------------------------------------------------
# Successful run
# ---------------------------------------------------------------------------


def test_run_analysis_success_creates_all_entities(client: TestClient) -> None:
    ids = _seed("ok_user")
    resp = _run(client, ids["job_id"], ids["resume_version_id"], "ok_user")
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Response shape (design §5.1).
    assert body["agent_run"]["status"] == "succeeded"
    assert body["agent_run"]["workflow_type"] == "resume_aware_jd_analysis"
    assert body["agent_run"]["id"]
    assert body["analysis"]["job_id"] == ids["job_id"]
    assert body["analysis"]["agent_run_id"] == body["agent_run"]["id"]
    assert body["artifact"]["artifact_type"] == "jd_analysis"
    assert body["artifact"]["prompt_version"] == "jd-analysis-v2"
    assert body["artifact"]["model_name"]
    # Structured output is echoed and validated.
    structured = body["structured"]
    assert structured["match_score"] is not None
    assert 0 <= structured["match_score"] <= 100

    # Artifact source provenance (design §4/§5.1).
    source_ids = body["artifact"]["source_ids"]
    assert source_ids["job_id"] == ids["job_id"]
    assert source_ids["resume_version_id"] == ids["resume_version_id"]
    assert source_ids["resume_id"] == ids["resume_id"]
    assert source_ids["user_id"] == "ok_user"
    assert source_ids["model_request_id"]
    assert source_ids["provider"]

    # AgentRun.result carries the source-context + output IDs (design §5.1, §7).
    result = body["agent_run"]["result"]
    assert result["job_id"] == ids["job_id"]
    assert result["resume_version_id"] == ids["resume_version_id"]
    assert result["analysis_id"] == body["analysis"]["id"]
    assert result["artifact_id"] == body["artifact"]["id"]
    assert "source_context" in result
    assert "truncation" in result["source_context"]

    # DB: all six step names exist for this run, all succeeded, ordered.
    run_id = body["agent_run"]["id"]
    with SessionLocal() as db:
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        step_names = [s.name for s in steps]
        assert step_names == [
            "load_context",
            "build_prompt_context",
            "call_model",
            "validate_model_output",
            "persist_outputs",
            "complete_run",
        ]
        assert all(s.status == "succeeded" for s in steps)

        analysis = db.get(JobAnalysis, body["analysis"]["id"])
        assert analysis is not None
        assert analysis.job_id == ids["job_id"]
        # Scores coerce int -> float (column is Float).
        assert isinstance(analysis.match_score, float)
        assert isinstance(analysis.risk_score, float)
        # JSON note blobs mapped from the validated output.
        assert analysis.salary_analysis == {"note": structured["salary_note"]}
        assert analysis.growth_analysis == {"note": structured["growth_note"]}
        assert analysis.stability_analysis == {"note": structured["stability_note"]}
        assert analysis.summary

        artifact = db.get(GeneratedArtifact, body["artifact"]["id"])
        assert artifact is not None
        assert artifact.artifact_type == "jd_analysis"
        assert artifact.prompt_version == "jd-analysis-v2"
        assert artifact.source_ids["job_id"] == ids["job_id"]
        assert artifact.source_ids["resume_version_id"] == ids["resume_version_id"]

        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"


def test_run_analysis_no_raw_prompt_or_resume_content_logged(client: TestClient) -> None:
    """No raw full prompt or full resume content may land in persisted rows."""
    raw_resume = "SUPER_SECRET_RESUME_TOKEN_42"
    ids = _seed("no_log_user", raw_text=raw_resume)
    resp = _run(client, ids["job_id"], ids["resume_version_id"], "no_log_user")
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["agent_run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        # AgentRun.result must not embed the raw resume text or the prompt body.
        result_blob = repr(run.result) + (run.error or "")
        assert "SUPER_SECRET_RESUME_TOKEN_42" not in result_blob
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        for step in steps:
            step_blob = repr(step.result) + (step.error or "")
            assert "SUPER_SECRET_RESUME_TOKEN_42" not in step_blob
        artifact = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert artifact is not None
        assert "SUPER_SECRET_RESUME_TOKEN_42" not in artifact.source_ids.__repr__()
        assert "SUPER_SECRET_RESUME_TOKEN_42" not in artifact.content


# ---------------------------------------------------------------------------
# Model validation failure -> 502
# ---------------------------------------------------------------------------


def test_run_analysis_model_invalid_returns_502_and_persists_failed_run(
    client: TestClient, invalid_gateway_override: _InvalidStubGateway
) -> None:
    ids = _seed("bad_model_user")
    resp = _run(client, ids["job_id"], ids["resume_version_id"], "bad_model_user")
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"] == "model returned invalid analysis"

    with SessionLocal() as db:
        runs = (
            db.execute(select(AgentRun).where(AgentRun.user_id == "bad_model_user")).scalars().all()
        )
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "failed"
        assert run.error == "model returned invalid analysis"
        assert run.result["failure"] == "model_invalid"
        # The failed run is scoped to the job so it can be listed per-job.
        assert run.job_id == ids["job_id"]

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run.id)).scalars().all()
        assert len(steps) >= 3
        # Validation failure is recorded on the validate_model_output step.
        validate_step = next(s for s in steps if s.name == "validate_model_output")
        assert validate_step.status == "failed"
        # No raw prompt/resume content in the failed step.
        assert "raw_text" not in (validate_step.result or {})

        # Failure path must NOT create JobAnalysis / GeneratedArtifact.
        analyses = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run.id))
            .scalars()
            .all()
        )
        assert analyses == []
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run.id))
            .scalars()
            .all()
        )
        assert artifacts == []


def test_run_analysis_model_call_failure_returns_502_and_persists_failed_run(
    client: TestClient, raising_gateway_override: _RaisingStubGateway
) -> None:
    """A gateway that raises drives the call_model-failure 502 path (R4).

    The failed run + failed call_model step are persisted, no analysis/artifact
    rows are created, and the run is scoped to the job via ``job_id``.
    """
    ids = _seed("raising_user")
    resp = _run(client, ids["job_id"], ids["resume_version_id"], "raising_user")
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"] == "model call failed"

    with SessionLocal() as db:
        runs = (
            db.execute(select(AgentRun).where(AgentRun.user_id == "raising_user")).scalars().all()
        )
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "failed"
        assert run.error == "model call failed"
        assert run.result["failure"] == "model_invalid"
        assert run.job_id == ids["job_id"]

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run.id)).scalars().all()
        assert len(steps) >= 3
        call_step = next(s for s in steps if s.name == "call_model")
        assert call_step.status == "failed"
        assert call_step.result["provider"] == "stub-raising"
        assert "error_type" in call_step.result

        analyses = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run.id))
            .scalars()
            .all()
        )
        assert analyses == []
        artifacts = (
            db.execute(select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run.id))
            .scalars()
            .all()
        )
        assert artifacts == []


def test_failed_run_is_visible_via_agent_runs_job_filter(
    client: TestClient, raising_gateway_override: _RaisingStubGateway
) -> None:
    """Failure-visibility contract (R4): a failed run that created no
    ``JobAnalysis`` row is still findable via ``GET /agent-runs?job_id=…`` and
    its failed step metadata is readable via ``GET /agent-runs/{id}/detail``.
    """
    ids = _seed("visibility_user")
    resp = _run(client, ids["job_id"], ids["resume_version_id"], "visibility_user")
    assert resp.status_code == 502, resp.text

    # GET /jobs/{job_id}/analyses is still empty (no JobAnalysis was created).
    analyses_resp = client.get(
        f"/api/v1/jobs/{ids['job_id']}/analyses",
        headers=_headers("visibility_user"),
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
        headers=_headers("visibility_user"),
    )
    assert runs_resp.status_code == 200, runs_resp.text
    runs_body = runs_resp.json()
    assert runs_body["meta"]["total"] == 1
    run_summary = runs_body["items"][0]
    assert run_summary["status"] == "failed"
    assert run_summary["created_at"] is not None
    run_id = run_summary["id"]

    # GET /agent-runs/{run_id}/detail shows the failed call_model step and its
    # sanitized metadata.
    detail_resp = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("visibility_user"),
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["created_at"] is not None
    call_step = next(s for s in detail["steps"] if s["name"] == "call_model")
    assert call_step["status"] == "failed"
    assert call_step["result"]["provider"] == "stub-raising"
    assert "error_type" in call_step["result"]
    # created_at is now exposed on steps too.
    assert call_step["created_at"] is not None


# ---------------------------------------------------------------------------
# Ownership / data-quality errors
# ---------------------------------------------------------------------------


def test_run_analysis_404_for_missing_job(client: TestClient) -> None:
    ids = _seed("no_job_user")
    resp = _run(client, "nonexistent_job", ids["resume_version_id"], "no_job_user")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"


def test_run_analysis_404_for_cross_user_job(client: TestClient) -> None:
    ids_owner = _seed("owner_x")
    ids_intruder = _seed("intruder_x")
    resp = _run(
        client,
        ids_owner["job_id"],
        ids_intruder["resume_version_id"],
        "intruder_x",
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"


def test_run_analysis_404_for_missing_resume_version(client: TestClient) -> None:
    ids = _seed("no_ver_user")
    resp = _run(client, ids["job_id"], "nonexistent_ver", "no_ver_user")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume version not found"


def test_run_analysis_404_for_cross_user_resume_version(client: TestClient) -> None:
    ids_owner = _seed("rv_owner")
    ids_intruder = _seed("rv_intruder")
    resp = _run(
        client,
        ids_intruder["job_id"],
        ids_owner["resume_version_id"],
        "rv_intruder",
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume version not found"


def test_run_analysis_422_for_empty_raw_text(client: TestClient) -> None:
    ids = _seed("empty_text_user", raw_text="")
    resp = _run(client, ids["job_id"], ids["resume_version_id"], "empty_text_user")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "resume version has no parsed text"


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


def test_list_analyses_pagination_and_user_scoping(client: TestClient) -> None:
    ids = _seed("list_user")
    # Create three analyses for the owner.
    for _ in range(3):
        resp = _run(client, ids["job_id"], ids["resume_version_id"], "list_user")
        assert resp.status_code == 201, resp.text

    # page_size=2 -> 2 items, total=3.
    resp = client.get(
        f"/api/v1/jobs/{ids['job_id']}/analyses",
        params={"page": 1, "page_size": 2},
        headers=_headers("list_user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["total"] == 3
    assert len(body["items"]) == 2
    # Newest first: created_at descending (on the nested analysis object).
    assert body["items"][0]["analysis"]["created_at"] >= body["items"][1]["analysis"]["created_at"]

    # page 2 -> the remaining 1 item.
    resp2 = client.get(
        f"/api/v1/jobs/{ids['job_id']}/analyses",
        params={"page": 2, "page_size": 2},
        headers=_headers("list_user"),
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 1


def test_list_analyses_returns_persisted_result_for_hydration(client: TestClient) -> None:
    """R1/R2: a persisted analysis round-trips through the list endpoint with
    enough data (analysis + artifact + structured) to reconstruct the UI
    without the original POST response.
    """
    ids = _seed("hydrate_user")
    run_resp = _run(client, ids["job_id"], ids["resume_version_id"], "hydrate_user")
    assert run_resp.status_code == 201, run_resp.text
    run_id = run_resp.json()["agent_run"]["id"]

    # Fresh read — do not rely on the POST response state.
    resp = client.get(
        f"/api/v1/jobs/{ids['job_id']}/analyses",
        headers=_headers("hydrate_user"),
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


def test_run_detail_returns_ordered_steps_user_scoped(client: TestClient) -> None:
    """R3/R5: run detail with ordered steps, scoped to the current user."""
    ids = _seed("detail_user")
    run_resp = _run(client, ids["job_id"], ids["resume_version_id"], "detail_user")
    assert run_resp.status_code == 201, run_resp.text
    run_id = run_resp.json()["agent_run"]["id"]

    # Owner sees the full step trail.
    owner = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("detail_user"),
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
    # created_at is exposed on the run and each step (timing/auditability).
    assert detail["created_at"] is not None
    assert all(s["created_at"] is not None for s in detail["steps"])

    # Cross-user access is 404 (not 403) — R5.
    intruder = client.get(
        f"/api/v1/agent-runs/{run_id}/detail",
        headers=_headers("detail_intruder"),
    )
    assert intruder.status_code == 404


def test_list_analyses_404_for_cross_user_job(client: TestClient) -> None:
    ids_owner = _seed("list_owner")
    _seed("list_intruder")
    resp = client.get(
        f"/api/v1/jobs/{ids_owner['job_id']}/analyses",
        headers=_headers("list_intruder"),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "job not found"
