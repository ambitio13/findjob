"""API + worker integration tests for the JD paste parse flow.

The parse endpoint uses the create-job-first pattern (design.md):

- ``POST /jobs/parse`` creates a ``JobPosting`` up front (company/title set to
  a ``"(解析中…)"`` placeholder, ``jd_raw`` persisted) so the job appears in
  the list immediately and the frontend can close the create modal without
  blocking. It then creates a ``queued`` ``AgentRun`` (linked to the job via
  ``job_id``) and enqueues a ``JdPasteParsePayload`` to the worker, returning
  **immediately** with HTTP 202 + ``JdParseSubmitResponse`` (run summary + job
  + echoed raw_jd/platform). It does **not** block on the model call.
- The worker handler ``jd_paste_parsing`` executes the parse workflow
  (``parse_jd_with_run``): flips the run queued → running → succeeded/failed,
  persists six sanitized ``AgentStep`` rows, and on success writes the parsed
  fields back to ``JobPosting.jd_normalized`` and overwrites the placeholder
  company/title so the job list reflects the parse result.

Tests cover both layers:

API layer (HTTP 202 contract):

- successful submit: 202, ``run.status == "queued"``, raw_jd/platform echoed,
  a ``JobPosting`` row created with placeholder company/title and ``jd_raw``
  persisted, and an ``AgentRun`` row persisted in ``queued`` state linked to
  the job via ``job_id`` with sanitized result (raw_jd_len, not raw text).
- enqueue failure (Redis down): the run is flipped to ``failed`` before
  returning so the frontend never polls forever.
- blank ``raw_jd`` → 422 and no ``AgentRun`` created.
- ``PATCH /jobs/{id}`` overwrites editable fields.
- ``POST /jobs`` with ``jd_normalized`` → 201; ``GET /jobs/{id}`` echoes it.
- ``POST /jobs`` without ``jd_normalized`` → 201, ``jd_normalized`` is null.

Worker layer (handler execution):

- successful parse via the ``jd_paste_parsing`` handler: run succeeds, six
  ordered steps persisted, ``AgentRun.result.fields`` populated, the linked
  ``JobPosting.jd_normalized`` is written + placeholder company/title
  overwritten, sanitization (no raw JD token in any persisted row).
- model-invalid output (stub gateway) → run failed, ``validate_model_output``
  step failed, ``result.fields`` absent, no ``jd_normalized`` write-back.
- gateway raises → run failed, ``call_model`` step failed.
- ownership mismatch → run failed with sanitized error.
- write-back ownership mismatch (foreign ``job_id``, or cross-user job) → run
  failed, victim job row not mutated (regression for cross-user write risk).
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

from app.db.models.models import AgentRun, AgentStep, JobPosting
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
    run_id: str,
    user_id: str,
    raw_jd: str,
    platform: str | None = None,
    *,
    job_id: str | None = None,
) -> JdPasteParsePayload:
    return JdPasteParsePayload(
        workflow_type="jd_paste_parsing",
        user_id=user_id,
        agent_run_id=run_id,
        idempotency_key=f"jd_paste:{run_id}",
        raw_jd=raw_jd,
        platform=platform,
        job_id=job_id,
    )


def _seed_user(user_id: str) -> None:
    """Insert a ``UserProfile`` row (FK target for ``job_postings.user_id``)."""
    from app.db.models.models import UserProfile

    with SessionLocal() as db:
        db.add(UserProfile(id=user_id, display_name=f"用户 {user_id}"))
        db.commit()


def _create_placeholder_job(
    user_id: str, raw_jd: str, *, platform: str = "manual"
) -> tuple[str, str]:
    """Insert a ``(解析中…)`` placeholder ``JobPosting`` + linked ``queued`` run.

    Mirrors the create-job-first endpoint: job row is created up front with a
    placeholder company/title, and a ``jd_paste_parsing`` ``AgentRun`` is
    created in the ``queued`` state and linked via ``job_id``. Returns
    ``(run_id, job_id)`` so worker tests can pass ``job_id`` on the payload.
    """
    from app.db.repositories import agent_run_repo

    with SessionLocal() as db:
        job = JobPosting(
            user_id=user_id,
            platform=platform,
            company="(解析中…)",
            title="(解析中…)",
            jd_raw=raw_jd,
        )
        db.add(job)
        db.flush()
        job_id = job.id
        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="jd_paste_parsing",
            status="queued",
            job_id=job_id,
            result={
                "user_id": user_id,
                "raw_jd_len": len(raw_jd),
                "platform": platform if platform != "manual" else None,
                "job_id": job_id,
            },
        )
        db.commit()
        return run.id, job_id


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

    # create-job-first: a JobPosting is created up front with the placeholder
    # company/title and ``jd_raw`` persisted, so the job appears in the list
    # immediately and the frontend can close the modal without blocking.
    job = body["job"]
    job_id = job["id"]
    assert job["company"] == "(解析中…)"
    assert job["title"] == "(解析中…)"
    assert job["jd_raw"] == raw_jd
    assert job["platform"] == "boss"
    assert job["jd_normalized"] is None

    with SessionLocal() as db:
        # DB: the JobPosting row matches the echoed job.
        job_row = db.get(JobPosting, job_id)
        assert job_row is not None
        assert job_row.user_id == "jp_ok"
        assert job_row.company == "(解析中…)"
        assert job_row.title == "(解析中…)"
        assert job_row.jd_raw == raw_jd

        # DB: a queued AgentRun with sanitized metadata (raw_jd_len, not raw
        # text), linked to the job via ``job_id``.
        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "queued"
        assert run_row.workflow_type == "jd_paste_parsing"
        assert run_row.user_id == "jp_ok"
        assert run_row.job_id == job_id
        assert run_row.result["raw_jd_len"] == len(raw_jd)
        assert run_row.result["platform"] == "boss"
        assert run_row.result["job_id"] == job_id
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
    # The placeholder job is still created with platform="manual" default.
    assert body["job"]["platform"] == "manual"


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
    job_id = body["job"]["id"]
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "queue enqueue failed"
        # The placeholder job remains so the user can still edit it manually.
        job = db.get(JobPosting, job_id)
        assert job is not None
        assert job.company == "(解析中…)"


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
# API layer: PATCH /jobs/{id} (edit fields, e.g. correct a parsed draft)
# ---------------------------------------------------------------------------


def test_update_job_overwrites_editable_fields(client: TestClient) -> None:
    """``PATCH /jobs/{id}`` applies only supplied fields; omitted fields stay."""
    create = client.post(
        "/api/v1/jobs",
        json={
            "company": "(解析中…)",
            "title": "(解析中…)",
            "jd_raw": "描述。",
        },
        headers=_headers("jp_patch"),
    )
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    patch = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={
            "company": "真公司",
            "title": "高级前端",
            "location": "上海",
            "salary_range": "30-50k",
            "direction": "前端",
            "platform": "boss",
        },
        headers=_headers("jp_patch"),
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["company"] == "真公司"
    assert body["title"] == "高级前端"
    assert body["location"] == "上海"
    assert body["salary_range"] == "30-50k"
    assert body["direction"] == "前端"
    assert body["platform"] == "boss"

    # The detail endpoint reflects the update.
    detail = client.get(f"/api/v1/jobs/{job_id}", headers=_headers("jp_patch"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["company"] == "真公司"


def test_update_job_omitted_fields_are_preserved(client: TestClient) -> None:
    """Only non-``None`` supplied fields are applied; others stay as-is."""
    create = client.post(
        "/api/v1/jobs",
        json={
            "company": "原公司",
            "title": "原职位",
            "jd_raw": "描述。",
            "location": "北京",
        },
        headers=_headers("jp_patch_partial"),
    )
    job_id = create.json()["id"]

    # Patch only the title; company + location must remain.
    patch = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"title": "新职位"},
        headers=_headers("jp_patch_partial"),
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["title"] == "新职位"
    assert body["company"] == "原公司"
    assert body["location"] == "北京"


def test_update_job_cross_user_returns_404(client: TestClient) -> None:
    """Cross-user PATCH returns 404 (not 403), consistent with other endpoints."""
    create = client.post(
        "/api/v1/jobs",
        json={"company": "A", "title": "B", "jd_raw": "c"},
        headers=_headers("jp_patch_owner"),
    )
    job_id = create.json()["id"]

    resp = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"company": "hijack"},
        headers=_headers("jp_patch_other"),
    )
    assert resp.status_code == 404


def test_update_job_explicit_null_clears_nullable_field(client: TestClient) -> None:
    """An explicit ``null`` clears a nullable column; an omitted key is a no-op.

    This is the regression test for the "clear field" bug: the old
    ``job_repo.update`` skipped ``None`` values, so sending
    ``{"location": null}`` was a no-op instead of clearing the city. The PATCH
    endpoint now uses ``JobUpdate.model_fields_set`` to pass only client-supplied
    keys, and the repository applies them verbatim (including ``None``).
    """
    create = client.post(
        "/api/v1/jobs",
        json={
            "company": "某公司",
            "title": "某职位",
            "jd_raw": "描述。",
            "location": "北京",
            "salary_range": "20-30k",
            "direction": "后端",
        },
        headers=_headers("jp_clear"),
    )
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    # Clear location + salary_range via explicit null; omit direction so it is
    # preserved.
    patch = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"location": None, "salary_range": None},
        headers=_headers("jp_clear"),
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["location"] is None
    assert body["salary_range"] is None
    # Omitted direction is preserved.
    assert body["direction"] == "后端"
    # Non-nullable company is preserved.
    assert body["company"] == "某公司"

    # Persisted: re-GET to confirm the DB row was actually cleared.
    detail = client.get(f"/api/v1/jobs/{job_id}", headers=_headers("jp_clear"))
    assert detail.status_code == 200, detail.text
    d = detail.json()
    assert d["location"] is None
    assert d["salary_range"] is None
    assert d["direction"] == "后端"


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
async def test_handler_success_writes_back_job_when_job_id_supplied() -> None:
    """create-job-first: when ``job_id`` is on the payload, the worker writes
    the parsed fields back to ``JobPosting.jd_normalized`` and overwrites the
    placeholder company/title so the job list reflects the parse result."""
    raw_jd = "资深后端工程师，负责平台 API。"
    _seed_user("jp_worker_writeback")
    run_id, job_id = _create_placeholder_job(
        "jp_worker_writeback", raw_jd, platform="boss"
    )

    payload = _make_payload(run_id, "jp_worker_writeback", raw_jd, platform="boss", job_id=job_id)
    result = await jd_paste_parsing({}, payload)
    assert result == run_id

    with SessionLocal() as db:
        job = db.get(JobPosting, job_id)
        assert job is not None
        # The placeholder company/title are overwritten with parsed values.
        assert job.company == "某科技公司"
        assert job.title == "后端工程师"
        # ``jd_normalized`` now carries the parsed fields + extraction block.
        assert job.jd_normalized is not None
        assert "fields" in job.jd_normalized
        assert job.jd_normalized["fields"]["title"] == "后端工程师"
        assert job.jd_normalized["fields"]["company"] == "某科技公司"
        assert "_extraction" in job.jd_normalized
        assert job.jd_normalized["_extraction"]["status"] == "succeeded"

        # The persist_outputs step recorded the write-back.
        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        persist_step = next(s for s in steps if s.name == "persist_outputs")
        assert persist_step.result["job_id"] == job_id
        assert persist_step.result["job_written"] is True


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
    """Model returns invalid JSON → run failed, validate_model_output step failed.

    When ``job_id`` is supplied the write-back path is skipped on failure, so
    ``jd_normalized`` stays null and the placeholder company/title remain.
    """
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _InvalidStubGateway(),
    )
    raw_jd = "某岗位描述。"
    _seed_user("jp_worker_inv")
    run_id, job_id = _create_placeholder_job("jp_worker_inv", raw_jd)
    payload = _make_payload(run_id, "jp_worker_inv", raw_jd, job_id=job_id)

    result = await jd_paste_parsing({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        # No fields on failure.
        assert "fields" not in (run.result or {})

        # The write-back was skipped: jd_normalized stays null and the
        # placeholder company/title are preserved so the user can edit them.
        job = db.get(JobPosting, job_id)
        assert job is not None
        assert job.jd_normalized is None
        assert job.company == "(解析中…)"
        assert job.title == "(解析中…)"

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


@pytest.mark.asyncio
async def test_handler_write_back_owner_mismatch_fails_run() -> None:
    """Regression: a payload carrying a foreign ``job_id`` must not write back.

    The worker's persist_outputs step must verify both that the job is owned by
    ``payload.user_id`` *and* that ``run.job_id == job_id`` (the run was linked
    to this job at enqueue time). A payload that supplies another user's job
    id — or a stale id from a different run — must fail the run loudly instead
    of silently mutating a foreign job row. Before the fix, ``db.get(JobPosting,
    job_id)`` was the only check and a cross-user id would have been written.
    """
    raw_jd = "资深后端工程师，负责平台 API。"
    # Victim: a placeholder job + linked run owned by jp_victim.
    _seed_user("jp_victim")
    run_id, victim_job_id = _create_placeholder_job("jp_victim", raw_jd)

    # Attacker: a separate queued run owned by jp_attacker, but the payload
    # points at the victim's job_id. The run is *not* linked to that job
    # (run.job_id is None), so the consistency check must catch it.
    _seed_user("jp_attacker")
    with SessionLocal() as db:
        from app.db.repositories import agent_run_repo

        attacker_run = agent_run_repo.create_run(
            db,
            user_id="jp_attacker",
            workflow_type="jd_paste_parsing",
            status="queued",
            result={"user_id": "jp_attacker", "raw_jd_len": len(raw_jd)},
        )
        db.commit()
        attacker_run_id = attacker_run.id

    payload = _make_payload(
        attacker_run_id, "jp_attacker", raw_jd, job_id=victim_job_id
    )
    result = await jd_paste_parsing({}, payload)

    # The handler's generic except flips the run to failed.
    assert result == "failed"
    with SessionLocal() as db:
        run = db.get(AgentRun, attacker_run_id)
        assert run is not None
        assert run.status == "failed"
        # The victim's job must NOT have been mutated.
        victim = db.get(JobPosting, victim_job_id)
        assert victim is not None
        assert victim.jd_normalized is None
        assert victim.company == "(解析中…)"
        assert victim.title == "(解析中…)"


@pytest.mark.asyncio
async def test_handler_write_back_cross_user_job_fails_run() -> None:
    """Regression: even when ``run.job_id`` matches, a cross-user job must fail.

    This covers the case where an attacker somehow enqueues a run whose
    ``job_id`` points at another user's job. The ownership check
    (``job.user_id == user_id``) must catch it.
    """
    raw_jd = "资深后端工程师，负责平台 API。"
    _seed_user("jp_victim2")
    _seed_user("jp_attacker2")
    # Victim owns the job; attacker owns the run but links it to the victim's
    # job (simulating a tampered payload at enqueue time).
    with SessionLocal() as db:
        from app.db.repositories import agent_run_repo

        victim_job = JobPosting(
            user_id="jp_victim2",
            platform="manual",
            company="(解析中…)",
            title="(解析中…)",
            jd_raw=raw_jd,
        )
        db.add(victim_job)
        db.flush()
        victim_job_id = victim_job.id
        attacker_run = agent_run_repo.create_run(
            db,
            user_id="jp_attacker2",
            workflow_type="jd_paste_parsing",
            status="queued",
            job_id=victim_job_id,
            result={"user_id": "jp_attacker2", "raw_jd_len": len(raw_jd)},
        )
        db.commit()
        attacker_run_id = attacker_run.id

    payload = _make_payload(
        attacker_run_id, "jp_attacker2", raw_jd, job_id=victim_job_id
    )
    result = await jd_paste_parsing({}, payload)

    assert result == "failed"
    with SessionLocal() as db:
        run = db.get(AgentRun, attacker_run_id)
        assert run is not None
        assert run.status == "failed"
        victim = db.get(JobPosting, victim_job_id)
        assert victim is not None
        assert victim.jd_normalized is None
        assert victim.user_id == "jp_victim2"


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
