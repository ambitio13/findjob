"""API + worker integration tests for the resume fact extraction queue flow.

The upload and re-extract endpoints now use the enqueue-and-poll pattern
(design.md for ``08-01-async-resume-fact-extraction``):

- ``POST /resumes`` saves the file + version, creates a ``queued``
  ``AgentRun`` (``workflow_type="resume_fact_extraction"``), writes
  ``_extraction.status="pending"``, enqueues a
  ``ResumeFactExtractionPayload`` to the worker queue, and returns
  immediately with HTTP 201 + ``ResumeDetailOut``. It does **not** block on
  the model call.
- ``POST /resumes/{id}/versions/{vid}/extract`` follows the same pattern:
  creates a fresh ``queued`` run, marks pending, enqueues, and returns
  immediately with HTTP 202.
- The worker handler ``resume_fact_extraction`` executes the extraction
  workflow (``extract_resume_facts_with_run``): flips the run queued →
  running → succeeded/failed, persists six sanitized ``AgentStep`` rows,
  and on success stores typed ``facts`` + ``_extraction`` status into
  ``ResumeVersion.parsed_facts``.

Tests cover both layers:

API layer (HTTP 201/202 contract):

- successful upload: 201, ``_extraction.status == "pending"``, a ``queued``
  ``AgentRun`` row persisted with sanitized result (raw_text_len, not text).
- upload with enqueue failure (Redis down): the run is flipped to ``failed``
  so the frontend never polls forever.
- unsupported format (.rtf): ``_extraction.status == "not_run"``, no run
  created, no enqueue.
- re-extract: 202, fresh ``queued`` run, ``_extraction.status == "pending"``.
- re-extract on unsupported (empty raw_text): ``not_run``, no run.
- re-extract cross-user: 404.

Worker layer (handler execution):

- successful extraction via the ``resume_fact_extraction`` handler: run
  succeeds, six ordered steps persisted, typed facts written,
  ``_extraction.status == "succeeded"``, sanitization (no raw token in any
  persisted row).
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

from app.db.models.models import AgentRun, AgentStep, Resume, ResumeVersion, UserProfile
from app.db.repositories import agent_run_repo
from app.db.session import SessionLocal
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.queue.handlers import resume_fact_extraction
from app.queue.payloads import ResumeFactExtractionPayload

SECRET = "SUPER_SECRET_RESUME_TOKEN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _txt_bytes() -> bytes:
    return f"name: {SECRET}\nPython 后端工程师\n5 年经验".encode()


def _upload(client: TestClient, filename: str, content: bytes, user: str):
    return client.post(
        "/api/v1/resumes",
        files={"file": (filename, content, "application/octet-stream")},
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


def _extraction_status(client: TestClient, resume_id: str, user: str) -> str:
    resp = client.get(f"/api/v1/resumes/{resume_id}", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["latest_version"]["parsed_facts"]["_extraction"]["status"]


# ---------------------------------------------------------------------------
# Stub gateways for the worker failure path
# ---------------------------------------------------------------------------


class _RaisingStubGateway(ModelGateway):
    """Gateway whose ``chat()`` raises, to drive the model-call-failure path."""

    provider_name = "stub-raising"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("simulated provider outage")


def _create_resume_with_version(
    user_id: str, raw_text: str, filename: str = "r.txt"
) -> tuple[str, str, str]:
    """Insert a Resume + ResumeVersion + UserProfile and return (resume_id, version_id, user_id)."""
    with SessionLocal() as db:
        user = UserProfile(id=user_id, display_name="测试用户")
        db.add(user)
        db.flush()
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
        db.commit()
        return resume.id, version.id, user_id


def _create_queued_resume_run(
    user_id: str, resume_id: str, version_id: str, raw_text_len: int
) -> str:
    """Insert a queued resume_fact_extraction AgentRun and return its id (committed).

    Mirrors what the API endpoint does: creates the run with sanitized metadata
    *before* the worker picks it up.
    """
    with SessionLocal() as db:
        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="resume_fact_extraction",
            status="queued",
            result={
                "resume_id": resume_id,
                "resume_version_id": version_id,
                "raw_text_len": raw_text_len,
            },
        )
        db.commit()
        return run.id


def _make_payload(
    run_id: str, user_id: str, resume_id: str, version_id: str
) -> ResumeFactExtractionPayload:
    return ResumeFactExtractionPayload(
        workflow_type="resume_fact_extraction",
        user_id=user_id,
        agent_run_id=run_id,
        idempotency_key=f"resume_fact:{run_id}",
        resume_id=resume_id,
        version_id=version_id,
    )


# ---------------------------------------------------------------------------
# API layer: successful upload (HTTP 201, pending, queued run, sanitized)
# ---------------------------------------------------------------------------


def test_upload_enqueues_extraction_and_returns_pending(client: TestClient) -> None:
    """Upload creates a queued run, marks pending, enqueues, returns immediately."""
    with _patch_get_queue_ok():
        resp = _upload(client, "resume.txt", _txt_bytes(), user="ru_ok")
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # The upload response must not block on extraction: status is pending.
    extraction = body["latest_version"]["parsed_facts"]["_extraction"]
    assert extraction["status"] == "pending"
    assert "run_id" in extraction
    run_id = extraction["run_id"]

    # DB: a queued AgentRun with sanitized metadata (raw_text_len, not text).
    with SessionLocal() as db:
        run_row = db.get(AgentRun, run_id)
        assert run_row is not None
        assert run_row.status == "queued"
        assert run_row.workflow_type == "resume_fact_extraction"
        assert run_row.user_id == "ru_ok"
        assert run_row.result["raw_text_len"] == len(_txt_bytes().decode())
        # Sanitization: the secret must not appear in the persisted result.
        run_blob = json.dumps(run_row.result or {}, ensure_ascii=False) + (run_row.error or "")
        assert SECRET not in run_blob


def test_upload_rtf_skips_extraction_not_run(client: TestClient) -> None:
    """Unsupported format → not_run, no AgentRun created, no enqueue."""
    with _patch_get_queue_ok() as mock_queue:
        resp = _upload(client, "resume.rtf", b"{\\rtf1 legacy}", user="ru_rtf")
    assert resp.status_code == 201, resp.text
    facts = resp.json()["latest_version"]["parsed_facts"]
    assert facts["_extraction"]["status"] == "not_run"
    assert "run_id" not in (facts["_extraction"] or {})

    # No enqueue was attempted.
    mock_queue.assert_not_called()

    # No AgentRun row created.
    with SessionLocal() as db:
        rows = db.execute(select(AgentRun).where(AgentRun.user_id == "ru_rtf")).scalars().all()
        assert len(rows) == 0


def test_upload_enqueue_failure_flips_run_to_failed(client: TestClient) -> None:
    """When Redis is unavailable the endpoint flips the run to ``failed``."""
    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = _upload(client, "resume.txt", _txt_bytes(), user="ru_redis")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    extraction = body["latest_version"]["parsed_facts"]["_extraction"]
    # The frontend polls parsed_facts._extraction, so enqueue failure must be
    # terminal there too (not just on AgentRun).
    assert extraction["status"] == "failed"
    run_id = extraction["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "queue enqueue failed"


def test_reextract_enqueue_failure_marks_extraction_failed(client: TestClient) -> None:
    """Re-extract enqueue failure must not leave the version pending forever."""
    with _patch_get_queue_ok():
        upload = _upload(client, "resume.txt", _txt_bytes(), user="ru_re_redis")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    with patch(
        "app.queue.runtime.get_queue",
        new=AsyncMock(side_effect=OSError("redis down")),
    ):
        resp = client.post(
            f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
            headers=_headers("ru_re_redis"),
        )
    assert resp.status_code == 202, resp.text
    extraction = resp.json()["latest_version"]["parsed_facts"]["_extraction"]
    assert extraction["status"] == "failed"
    run_id = extraction["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "queue enqueue failed"


def test_upload_run_is_scoped_to_current_user(client: TestClient) -> None:
    with _patch_get_queue_ok():
        resp = _upload(client, "resume.txt", _txt_bytes(), user="ru_owner")
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["latest_version"]["parsed_facts"]["_extraction"]["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.user_id == "ru_owner"


# ---------------------------------------------------------------------------
# API layer: re-extract (HTTP 202, fresh queued run, pending)
# ---------------------------------------------------------------------------


def test_reextract_enqueues_fresh_run_and_returns_202(client: TestClient) -> None:
    """Re-extract creates a fresh queued run and returns immediately."""
    # Upload first to get a resume + version.
    with _patch_get_queue_ok():
        upload = _upload(client, "resume.txt", _txt_bytes(), user="ru_re")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]
    first_run_id = body["latest_version"]["parsed_facts"]["_extraction"]["run_id"]

    # Re-extract.
    with _patch_get_queue_ok():
        resp = client.post(
            f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
            headers=_headers("ru_re"),
        )
    assert resp.status_code == 202, resp.text
    rbody = resp.json()
    extraction = rbody["latest_version"]["parsed_facts"]["_extraction"]
    assert extraction["status"] == "pending"
    new_run_id = extraction["run_id"]
    # A fresh run was created, distinct from the upload run.
    assert new_run_id != first_run_id

    with SessionLocal() as db:
        run = db.get(AgentRun, new_run_id)
        assert run is not None
        assert run.status == "queued"
        assert run.workflow_type == "resume_fact_extraction"
        assert run.user_id == "ru_re"


def test_reextract_on_empty_raw_text_returns_not_run(client: TestClient) -> None:
    """Re-extract on an unsupported version → not_run, no run created."""
    with _patch_get_queue_ok() as mock_queue:
        upload = _upload(client, "resume.rtf", b"{\\rtf1 legacy}", user="ru_empty_re")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    mock_queue.reset_mock()
    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
        headers=_headers("ru_empty_re"),
    )
    assert resp.status_code == 202, resp.text
    extraction = resp.json()["latest_version"]["parsed_facts"]["_extraction"]
    assert extraction["status"] == "not_run"
    # No enqueue was attempted.
    mock_queue.assert_not_called()


def test_reextract_404_for_other_user(client: TestClient) -> None:
    with _patch_get_queue_ok():
        upload = _upload(client, "resume.txt", _txt_bytes(), user="owner_re2")
    assert upload.status_code == 201
    body = upload.json()
    resume_id = body["id"]
    version_id = body["latest_version"]["id"]

    resp = client.post(
        f"/api/v1/resumes/{resume_id}/versions/{version_id}/extract",
        headers=_headers("intruder_re2"),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Worker layer: successful extraction via resume_fact_extraction handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_success_marks_run_succeeded_with_facts() -> None:
    """The handler drives the full extraction workflow and writes typed facts."""
    raw_text = f"name: {SECRET}\nPython 后端工程师\n5 年经验"
    resume_id, version_id, user_id = _create_resume_with_version("ru_worker_ok", raw_text)
    run_id = _create_queued_resume_run(user_id, resume_id, version_id, len(raw_text))
    payload = _make_payload(run_id, user_id, resume_id, version_id)

    result = await resume_fact_extraction({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.workflow_type == "resume_fact_extraction"

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

        # Typed facts + _extraction written into ResumeVersion.parsed_facts.
        version = db.get(ResumeVersion, version_id)
        assert version is not None
        facts = version.parsed_facts or {}
        assert facts["_extraction"]["status"] == "succeeded"
        assert facts["_extraction"]["run_id"] == run_id
        assert facts["facts"]["contact"]["name"] == "张三"
        assert "Python" in facts["facts"]["skills"]

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
    raw_text = "name: 李四\n前端工程师"
    resume_id, version_id, user_id = _create_resume_with_version("ru_worker_dict", raw_text)
    run_id = _create_queued_resume_run(user_id, resume_id, version_id, len(raw_text))
    payload_dict = {
        "workflow_type": "resume_fact_extraction",
        "user_id": user_id,
        "agent_run_id": run_id,
        "idempotency_key": f"resume_fact:{run_id}",
        "resume_id": resume_id,
        "version_id": version_id,
    }

    result = await resume_fact_extraction({"job_id": "arq-job-1"}, payload_dict)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        version = db.get(ResumeVersion, version_id)
        assert version is not None
        facts = (version.parsed_facts or {}).get("facts", {})
        assert facts.get("contact", {}).get("name") == "张三"


# ---------------------------------------------------------------------------
# Worker layer: recoverable failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_failure_raising_gateway_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway raises → run failed, call_model step failed."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _RaisingStubGateway(),
    )
    raw_text = "name: 王五\n测试工程师"
    resume_id, version_id, user_id = _create_resume_with_version("ru_worker_raise", raw_text)
    run_id = _create_queued_resume_run(user_id, resume_id, version_id, len(raw_text))
    payload = _make_payload(run_id, user_id, resume_id, version_id)

    result = await resume_fact_extraction({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "call_model"

        version = db.get(ResumeVersion, version_id)
        assert version is not None
        assert (version.parsed_facts or {}).get("_extraction", {}).get("status") == "failed"


# ---------------------------------------------------------------------------
# Worker layer: ownership mismatch + missing run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_owner_mismatch_fails_run() -> None:
    """A cross-user payload must not execute; the run is failed with a sanitized error."""
    raw_text = "name: 赵六\n运维工程师"
    resume_id, version_id, user_id = _create_resume_with_version("ru_real_owner", raw_text)
    run_id = _create_queued_resume_run(user_id, resume_id, version_id, len(raw_text))
    # Payload claims a different user.
    payload = _make_payload(run_id, "ru_attacker", resume_id, version_id)

    result = await resume_fact_extraction({}, payload)

    assert result == "ownership_mismatch"
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "ownership mismatch"


@pytest.mark.asyncio
async def test_handler_missing_run_returns_marker() -> None:
    """A missing run id degrades gracefully instead of raising."""
    resume_id, version_id, user_id = _create_resume_with_version("ru_ghost", "some text")
    payload = _make_payload("does_not_exist", user_id, resume_id, version_id)
    result = await resume_fact_extraction({}, payload)
    assert result == "missing_run"


# ---------------------------------------------------------------------------
# Worker layer: sanitization (no raw resume leakage in persisted rows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_sanitizes_raw_resume_from_all_persisted_rows() -> None:
    raw_text = f"name: {SECRET}\n机密字段请勿泄露\nPython 5年"
    resume_id, version_id, user_id = _create_resume_with_version("ru_worker_san", raw_text)
    run_id = _create_queued_resume_run(user_id, resume_id, version_id, len(raw_text))
    payload = _make_payload(run_id, user_id, resume_id, version_id)

    await resume_fact_extraction({}, payload)

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run_blob = json.dumps(run.result or {}, ensure_ascii=False) + (run.error or "")
        assert SECRET not in run_blob

        steps = db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        for s in steps:
            step_blob = json.dumps(s.result or {}, ensure_ascii=False) + (s.error or "")
            assert SECRET not in step_blob
