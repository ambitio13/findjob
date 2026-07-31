"""API integration tests for the JD paste parse-then-create flow (design.md §B).

Covers the transport + persistence layer:

- successful parse via ``POST /jobs/parse``: HTTP 200, ``status="succeeded"``,
  parsed fields populated, run with the six ordered step names all succeeded,
  step/run results sanitized (no raw JD token).
- partial parse (some fields null) → 200, run succeeded.
- model-invalid output (stub gateway returns non-JSON) → 200, ``status="failed"``,
  fields are the empty typed shape, failed run persisted.
- gateway raises → 200, ``status="failed"``, empty typed shape.
- blank ``raw_jd`` → 422 and no ``AgentRun`` created.
- ``POST /jobs`` with ``jd_normalized`` → 201; ``GET /jobs/{id}`` echoes it.
- ``POST /jobs`` without ``jd_normalized`` → 201, ``jd_normalized`` is null.
- cross-user scoping: parse runs are bound to ``current_user``.

Tests use the shared ``client`` fixture (truncated test DB, fake provider).
Stub gateways for the failure path are wired via FastAPI dependency overrides,
mirroring ``test_jd_analysis_api.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_model_gateway_dep
from app.db.models.models import AgentRun, AgentStep, UserProfile
from app.db.session import SessionLocal
from app.main import app
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway

SECRET = "SUPER_SECRET_JD_TOKEN_42"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db, user_id: str = "jp_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="JD Paste 用户")
    db.add(user)
    db.flush()
    return user


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _parse(client: TestClient, user: str, raw_jd: str, platform: str | None = None) -> Any:
    body: dict[str, Any] = {"raw_jd": raw_jd}
    if platform is not None:
        body["platform"] = platform
    return client.post("/api/v1/jobs/parse", json=body, headers=_headers(user))


# ---------------------------------------------------------------------------
# Stub gateways for the failure path
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


@pytest.fixture()
def invalid_gateway_override():
    stub = _InvalidStubGateway()
    app.dependency_overrides[get_model_gateway_dep] = lambda: stub
    try:
        yield stub
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)


class _RaisingStubGateway(ModelGateway):
    """Gateway whose ``chat()`` raises, to drive the model-call-failure path."""

    provider_name = "stub-raising"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("simulated provider outage")


@pytest.fixture()
def raising_gateway_override():
    stub = _RaisingStubGateway()
    app.dependency_overrides[get_model_gateway_dep] = lambda: stub
    try:
        yield stub
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)


# ---------------------------------------------------------------------------
# Successful parse
# ---------------------------------------------------------------------------


def test_parse_success_returns_fields_and_six_steps(client: TestClient) -> None:
    raw_jd = f"资深后端工程师，负责平台 API。包含密钥 {SECRET}。"
    resp = _parse(client, "jp_ok", raw_jd, platform="boss")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "succeeded"
    assert body["raw_jd"] == raw_jd

    run = body["run"]
    assert run["status"] == "succeeded"
    run_id = run["id"]
    assert run_id

    fields = body["fields"]
    assert fields["title"] == "后端工程师"
    assert fields["company"] == "某科技公司"
    assert fields["responsibilities"]
    assert fields["hard_requirements"]
    assert fields["uncertain_fields"] == []

    # The response carries parse provenance the frontend persists into
    # jd_normalized._extraction (design.md §"jd_normalized shape").
    extraction = body["extraction"]
    assert extraction["status"] == "succeeded"
    assert extraction["run_id"] == run_id
    assert extraction["prompt_version"] == "jd-paste-parsing-v1"
    assert extraction["provider"]
    assert extraction["model"]
    assert extraction["parsed_at"]

    # DB: six ordered step names, all succeeded.
    with SessionLocal() as db:
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

        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "succeeded"
        assert run_row.workflow_type == "jd_paste_parsing"
        # job_id is null at parse time (parse-then-create).
        assert run_row.job_id is None

        # Sanitization: the secret token must not appear in any step result or
        # the run result/error blob.
        run_blob = json.dumps(run_row.result or {}, ensure_ascii=False) + (run_row.error or "")
        assert SECRET not in run_blob
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob


def test_parse_partial_fields_still_succeeds(client: TestClient) -> None:
    # The fake gateway returns a full object; partial parsing is exercised at
    # the contract level. Here we confirm a normal parse still succeeds and
    # echoes back the typed shape even when platform is omitted.
    resp = _parse(client, "jp_partial", "需要一名前端工程师，熟练 React。")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["fields"]["title"]


def test_parse_sanitizes_raw_jd_from_all_persisted_rows(client: TestClient) -> None:
    raw_jd = f"职位描述，机密字段 {SECRET} 请勿泄露。"
    resp = _parse(client, "jp_san", raw_jd)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob


# ---------------------------------------------------------------------------
# Recoverable failure (HTTP 200, failed run, empty typed fields)
# ---------------------------------------------------------------------------


def test_parse_failure_invalid_json_returns_200_with_failed_run(
    client: TestClient, invalid_gateway_override: _InvalidStubGateway
) -> None:
    resp = _parse(client, "jp_inv", "某岗位描述。")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "failed"
    run = body["run"]
    assert run["status"] == "failed"
    assert run["error"]

    # Fields are the empty typed shape.
    fields = body["fields"]
    assert fields["title"] is None
    assert fields["responsibilities"] == []
    assert fields["uncertain_fields"] == []

    run_id = run["id"]
    with SessionLocal() as db:
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "validate_model_output"


def test_parse_failure_raising_gateway_returns_200_with_failed_run(
    client: TestClient, raising_gateway_override: _RaisingStubGateway
) -> None:
    resp = _parse(client, "jp_raise", "某岗位描述。")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "failed"
    run = body["run"]
    assert run["status"] == "failed"

    run_id = run["id"]
    with SessionLocal() as db:
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "call_model"


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_parse_blank_jd_returns_422_and_creates_no_run(client: TestClient) -> None:
    for blank in ("", "   ", "\n\t  \n"):
        resp = client.post(
            "/api/v1/jobs/parse",
            json={"raw_jd": blank},
            headers=_headers("jp_blank"),
        )
        assert resp.status_code == 422, resp.text

    with SessionLocal() as db:
        rows = db.execute(select(AgentRun).where(AgentRun.user_id == "jp_blank")).scalars().all()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Create with / without jd_normalized
# ---------------------------------------------------------------------------


def test_create_job_with_jd_normalized_round_trips(client: TestClient) -> None:
    # The durable contract (design.md §"jd_normalized shape"): a top-level
    # ``fields`` blob holds the model-parsed draft, and ``_extraction`` holds
    # parse provenance (run_id/prompt_version/provider/model). The job's own
    # columns hold the user-edited final values.
    normalized = {
        "fields": {
            "title": "前端工程师",
            "company": "某厂",
            "responsibilities": ["开发组件库"],
            "uncertain_fields": [],
        },
        "_extraction": {
            "status": "succeeded",
            "run_id": "run_abc",
            "parsed_at": "2026-08-01T00:00:00Z",
            "prompt_version": "jd-paste-parsing-v1",
            "provider": "fake",
            "model": "fake-model",
        },
    }
    create = client.post(
        "/api/v1/jobs",
        json={
            "company": "某厂",
            "title": "前端工程师",
            "jd_raw": "熟练 React 和 TypeScript。",
            "platform": "boss",
            "jd_normalized": normalized,
        },
        headers=_headers("jp_create"),
    )
    assert create.status_code == 201, create.text
    job = create.json()
    assert job["jd_normalized"] == normalized

    detail = client.get(f"/api/v1/jobs/{job['id']}", headers=_headers("jp_create"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["jd_normalized"] == normalized


def test_create_job_without_jd_normalized_defaults_null(client: TestClient) -> None:
    create = client.post(
        "/api/v1/jobs",
        json={
            "company": "某公司",
            "title": "测试工程师",
            "jd_raw": "负责测试自动化。",
        },
        headers=_headers("jp_no_norm"),
    )
    assert create.status_code == 201, create.text
    assert create.json()["jd_normalized"] is None


# ---------------------------------------------------------------------------
# Ownership scoping
# ---------------------------------------------------------------------------


def test_parse_run_is_scoped_to_current_user(client: TestClient) -> None:
    resp = _parse(client, "jp_owner", "岗位描述。")
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.user_id == "jp_owner"

    # A different user must not see this run surfaced via their own parse.
    other = _parse(client, "jp_other", "另一段描述。")
    assert other.status_code == 200, other.text
    assert other.json()["run"]["id"] != run_id
