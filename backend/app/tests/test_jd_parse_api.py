"""API + worker integration tests for the JD paste parse-then-create flow.

The parse endpoint now uses the enqueue-and-poll pattern (design.md):

- ``POST /jobs/parse`` creates a ``queued`` ``AgentRun``, enqueues a
  ``JdPasteParsePayload`` to the worker, and returns **immediately** with HTTP
  202 + ``JdParseSubmitResponse`` (run summary + echoed raw_jd/platform). It
  does **not** block on the model call.
- The worker handler ``jd_paste_parsing`` executes the parse workflow
  (``parse_jd_with_run``): flips the run queued → running → succeeded/failed,
  persists six sanitized ``AgentStep`` rows, and on success stores the parsed
  fields + extraction block in ``AgentRun.result`` so the frontend can hydrate
  them by polling ``GET /agent-runs/{id}/detail``.

Tests cover both layers:

API layer (HTTP 202 contract):

- successful submit: 202, ``run.status == "queued"``, raw_jd/platform echoed,
  an ``AgentRun`` row is persisted in ``queued`` state with sanitized result
  (raw_jd_len, not raw text).
- enqueue failure (Redis down): the run is flipped to ``failed`` before
  returning so the frontend never polls forever.
- blank ``raw_jd`` → 422 and no ``AgentRun`` created.
- ``POST /jobs`` with ``jd_normalized`` → 201; ``GET /jobs/{id}`` echoes it.
- ``POST /jobs`` without ``jd_normalized`` → 201, ``jd_normalized`` is null.

Worker layer (handler execution):

- successful parse via the ``jd_paste_parsing`` handler: run succeeds, six
  ordered steps persisted, ``AgentRun.result.fields`` populated, sanitization
  (no raw JD token in any persisted row).
- model-invalid output (stub gateway) → run failed, ``validate_model_output``
  step failed, ``result.fields`` absent.
- gateway raises → run failed, ``call_model`` step failed.
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

from app.db.models.models import AgentRun, AgentStep
from app.db.session import SessionLocal
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.queue.handlers import jd_paste_parsing
from app.queue.payloads import JdPasteParsePayload

SECRET = "SUPER_SECRET_JD_TOKEN_42"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _submit(client: TestClient, user: str, raw_jd: str, platform: str | None = None) -> Any:
    """POST /jobs/parse and return the response (enqueued, not yet executed)."""
    body: dict[str, Any] = {"raw_jd": raw_jd}
    if platform is not None:
        body["platform"] = platform
    return client.post("/api/v1/jobs/parse", json=body, headers=_headers(user))


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


def _create_queued_jd_run(user_id: str, raw_jd_len: int = 10, platform: str | None = None) -> str:
    """Insert a queued jd_paste_parsing AgentRun and return its id (committed).

    Mirrors what the API endpoint does: creates the run with sanitized metadata
    *before* the worker picks it up.
    """
    with SessionLocal() as db:
        from app.db.repositories import agent_run_repo

        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="jd_paste_parsing",
            status="queued",
            result={
                "user_id": user_id,
                "raw_jd_len": raw_jd_len,
                "platform": platform,
            },
        )
        db.commit()
        return run.id


def _make_payload(
    run_id: str, user_id: str, raw_jd: str, platform: str | None = None
) -> JdPasteParsePayload:
    return JdPasteParsePayload(
        workflow_type="jd_paste_parsing",
        user_id=user_id,
        agent_run_id=run_id,
        idempotency_key=f"jd_paste:{run_id}",
        raw_jd=raw_jd,
        platform=platform,
    )


# ---------------------------------------------------------------------------
# API layer: successful submit (HTTP 202, queued run, sanitized metadata)
# ---------------------------------------------------------------------------


def test_submit_returns_202_with_queued_run(client: TestClient) -> None:
    raw_jd = f"资深后端工程师，负责平台 API。包含密钥 {SECRET}。"
    with _patch_get_queue_ok():
        resp = _submit(client, "jp_ok", raw_jd, platform="boss")
    assert resp.status_code == 202, resp.text
    body = resp.json()

    # The submit response echoes raw_jd/platform and carries the run summary.
    assert body["raw_jd"] == raw_jd
    assert body["platform"] == "boss"
    run = body["run"]
    assert run["status"] == "queued"
    run_id = run["id"]
    assert run_id

    # DB: a queued AgentRun with sanitized metadata (raw_jd_len, not raw text).
    with SessionLocal() as db:
        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "queued"
        assert run_row.workflow_type == "jd_paste_parsing"
        assert run_row.user_id == "jp_ok"
        assert run_row.result["raw_jd_len"] == len(raw_jd)
        assert run_row.result["platform"] == "boss"
        # Sanitization: the secret must not appear in the persisted result.
        run_blob = json.dumps(run_row.result or {}, ensure_ascii=False) + (run_row.error or "")
        assert SECRET not in run_blob


def test_submit_without_platform_defaults_null(client: TestClient) -> None:
    with _patch_get_queue_ok():
        resp = _submit(client, "jp_noplat", "需要一名前端工程师。")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["platform"] is None
    assert body["run"]["status"] == "queued"


# ---------------------------------------------------------------------------
# API layer: enqueue failure flips run to failed
# ---------------------------------------------------------------------------


def test_submit_enqueue_failure_flips_run_to_failed(client: TestClient) -> None:
    """When Redis is unavailable the endpoint flips the run to ``failed``."""
    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = _submit(client, "jp_redis", "某岗位描述。")
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
# API layer: request validation
# ---------------------------------------------------------------------------


def test_submit_blank_jd_returns_422_and_creates_no_run(client: TestClient) -> None:
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
# API layer: create with / without jd_normalized
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
# API layer: ownership scoping
# ---------------------------------------------------------------------------


def test_submit_run_is_scoped_to_current_user(client: TestClient) -> None:
    with _patch_get_queue_ok():
        resp = _submit(client, "jp_owner", "岗位描述。")
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.user_id == "jp_owner"

    # A different user must not see this run surfaced via their own submit.
    with _patch_get_queue_ok():
        other = _submit(client, "jp_other", "另一段描述。")
    assert other.status_code == 202, other.text
    assert other.json()["run"]["id"] != run_id


# ---------------------------------------------------------------------------
# Worker layer: successful parse via jd_paste_parsing handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_success_marks_run_succeeded_with_fields() -> None:
    """The handler drives the full parse workflow and stores fields in result."""
    raw_jd = f"资深后端工程师，负责平台 API。包含密钥 {SECRET}。"
    run_id = _create_queued_jd_run("jp_worker_ok", raw_jd_len=len(raw_jd), platform="boss")
    payload = _make_payload(run_id, "jp_worker_ok", raw_jd, platform="boss")

    result = await jd_paste_parsing({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.workflow_type == "jd_paste_parsing"

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

        # The parsed fields + extraction are stored in result for frontend hydration.
        assert "fields" in run.result
        assert run.result["fields"]["title"] == "后端工程师"
        assert run.result["fields"]["company"] == "某科技公司"
        assert run.result["fields"]["responsibilities"]
        assert run.result["fields"]["hard_requirements"]
        assert run.result["fields"]["uncertain_fields"] == []

        assert "extraction" in run.result
        extraction = run.result["extraction"]
        assert extraction["status"] == "succeeded"
        assert extraction["run_id"] == run_id
        assert extraction["prompt_version"] == "jd-paste-parsing-v1"
        assert extraction["provider"]
        assert extraction["model"]

        # Sanitization: the secret token must not appear in any step result or
        # the run result/error blob.
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob


@pytest.mark.asyncio
async def test_handler_accepts_dict_payload() -> None:
    """arq delivers a deserialized dict; the handler must accept it."""
    raw_jd = "需要一名前端工程师，熟练 React。"
    run_id = _create_queued_jd_run("jp_worker_dict", raw_jd_len=len(raw_jd))
    payload_dict = {
        "workflow_type": "jd_paste_parsing",
        "user_id": "jp_worker_dict",
        "agent_run_id": run_id,
        "idempotency_key": f"jd_paste:{run_id}",
        "raw_jd": raw_jd,
        "platform": None,
    }

    result = await jd_paste_parsing({"job_id": "arq-job-1"}, payload_dict)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert "fields" in run.result


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
    raw_jd = "某岗位描述。"
    run_id = _create_queued_jd_run("jp_worker_inv", raw_jd_len=len(raw_jd))
    payload = _make_payload(run_id, "jp_worker_inv", raw_jd)

    result = await jd_paste_parsing({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        # No fields on failure.
        assert "fields" not in (run.result or {})

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "validate_model_output"


@pytest.mark.asyncio
async def test_handler_failure_raising_gateway_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway raises → run failed, call_model step failed."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    raw_jd = "某岗位描述。"
    run_id = _create_queued_jd_run("jp_worker_raise", raw_jd_len=len(raw_jd))
    payload = _make_payload(run_id, "jp_worker_raise", raw_jd)

    result = await jd_paste_parsing({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "call_model"


# ---------------------------------------------------------------------------
# Worker layer: ownership mismatch + missing run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_owner_mismatch_fails_run() -> None:
    """A cross-user payload must not execute; the run is failed with a sanitized error."""
    raw_jd = "某岗位描述。"
    run_id = _create_queued_jd_run("jp_real_owner", raw_jd_len=len(raw_jd))
    # Payload claims a different user.
    payload = _make_payload(run_id, "jp_attacker", raw_jd)

    result = await jd_paste_parsing({}, payload)

    assert result == "ownership_mismatch"
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "ownership mismatch"


@pytest.mark.asyncio
async def test_handler_missing_run_returns_marker() -> None:
    """A missing run id degrades gracefully instead of raising."""
    payload = _make_payload("does_not_exist", "jp_ghost", "某描述。")
    result = await jd_paste_parsing({}, payload)
    assert result == "missing_run"


# ---------------------------------------------------------------------------
# Worker layer: sanitization (no raw JD leakage in persisted rows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_sanitizes_raw_jd_from_all_persisted_rows() -> None:
    raw_jd = f"职位描述，机密字段 {SECRET} 请勿泄露。"
    run_id = _create_queued_jd_run("jp_worker_san", raw_jd_len=len(raw_jd))
    payload = _make_payload(run_id, "jp_worker_san", raw_jd)

    await jd_paste_parsing({}, payload)

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob
