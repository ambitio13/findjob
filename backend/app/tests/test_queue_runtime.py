"""Tests for the queue runtime foundation (prd acceptance criteria).

Covers:

- queue settings load with sane defaults;
- payload validation (typed base + smoke payload);
- enqueue success with a fake queue client (no real Redis needed);
- enqueue propagates Redis-unavailable errors instead of swallowing them;
- the smoke handler flips a persisted ``AgentRun`` queued → running → succeeded;
- the shared ``fail_run`` guard flips a run to ``failed`` with a sanitized error.

These tests never touch a real Redis instance. The handler/fail_run tests use
the same test PostgreSQL database as the rest of the suite (see ``conftest.py``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.db.repositories import agent_run_repo
from app.db.session import SessionLocal
from app.queue.handlers import fail_run, smoke
from app.queue.payloads import SmokePayload, WorkflowPayload
from app.queue.runtime import (
    _queue_name,
    _redis_settings_from_url,
    enqueue_workflow,
    get_redis_settings,
)

# ---------------------------------------------------------------------------
# 1: config
# ---------------------------------------------------------------------------


def test_queue_settings_have_defaults() -> None:
    settings = get_settings()
    assert settings.queue_namespace
    assert settings.queue_job_timeout > 0
    assert settings.queue_max_retries >= 0


def test_redis_settings_derived_from_redis_url() -> None:
    settings = get_redis_settings()
    # The default redis_url is redis://localhost:6379/0.
    assert settings.host == "localhost"
    assert settings.port == 6379
    assert settings.database == 0


def test_redis_settings_parses_db_index_and_password() -> None:
    rs = _redis_settings_from_url("redis://:secret@redis-host:6380/3")
    assert rs.host == "redis-host"
    assert rs.port == 6380
    assert rs.database == 3
    assert rs.password == "secret"


def test_queue_name_is_namespaced() -> None:
    name = _queue_name()
    assert name.startswith(get_settings().queue_namespace)
    assert name != "arq:queue"  # must not collide with arq's default


# ---------------------------------------------------------------------------
# 2: payload validation
# ---------------------------------------------------------------------------


def test_smoke_payload_validates_required_fields() -> None:
    payload = SmokePayload(
        user_id="u1",
        agent_run_id="run_1",
        idempotency_key="key_1",
    )
    assert payload.workflow_type == "smoke"
    dumped = payload.model_dump()
    assert dumped["user_id"] == "u1"
    assert dumped["agent_run_id"] == "run_1"
    assert dumped["idempotency_key"] == "key_1"


def test_smoke_payload_is_frozen() -> None:
    payload = SmokePayload(user_id="u1", agent_run_id="r", idempotency_key="k")
    with pytest.raises(ValidationError):
        payload.user_id = "mutated"  # type: ignore[misc]


def test_workflow_payload_requires_all_base_fields() -> None:
    with pytest.raises(ValidationError):
        WorkflowPayload()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 3: enqueue success (fake queue client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_workflow_calls_arq_with_payload() -> None:
    """enqueue_workflow passes the payload to arq under the workflow name."""
    payload = SmokePayload(user_id="u1", agent_run_id="run_1", idempotency_key="k1")
    fake_pool = AsyncMock()
    fake_pool.enqueue_job.return_value = AsyncMock(return_value="job_ref").return_value
    # Make enqueue_job return a truthy value.
    fake_pool.enqueue_job.return_value = object()

    with patch("app.queue.runtime.get_queue", new=AsyncMock(return_value=fake_pool)):
        result = await enqueue_workflow(payload, job_id="run_1")

    assert result is not None
    fake_pool.enqueue_job.assert_awaited_once()
    call_args = fake_pool.enqueue_job.call_args
    # First positional arg is the function name == workflow_type.
    assert call_args.args[0] == "smoke"
    # Second positional arg is the payload dict.
    assert call_args.args[1] == payload.model_dump()
    # job_id and queue_name are passed as kwargs.
    assert call_args.kwargs["_job_id"] == "run_1"
    assert call_args.kwargs["_queue_name"] == _queue_name()


# ---------------------------------------------------------------------------
# 4: Redis unavailable behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_workflow_propagates_redis_error() -> None:
    """When Redis is unreachable the helper raises; callers surface a failed run."""
    payload = SmokePayload(user_id="u1", agent_run_id="run_1", idempotency_key="k1")

    with patch("app.queue.runtime.get_queue", new=AsyncMock(side_effect=OSError("redis down"))):
        with pytest.raises(OSError, match="redis down"):
            await enqueue_workflow(payload)

    # The fake pool must never have been asked to enqueue a job.
    # (get_queue raised before enqueue_job could be called.)


# ---------------------------------------------------------------------------
# 5: smoke handler flips run to succeeded
# ---------------------------------------------------------------------------


def _create_queued_run(user_id: str) -> str:
    """Insert a queued AgentRun and return its id (committed)."""
    with SessionLocal() as db:
        run = agent_run_repo.create_run(
            db,
            user_id=user_id,
            workflow_type="smoke",
            status="queued",
        )
        db.commit()
        return run.id


@pytest.mark.asyncio
async def test_smoke_handler_marks_run_succeeded() -> None:
    run_id = _create_queued_run("u_smoke_ok")

    result = await smoke(
        {}, SmokePayload(user_id="u_smoke_ok", agent_run_id=run_id, idempotency_key="k")
    )

    assert result == run_id
    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.finished_at is not None
        assert run.result == {"workflow": "smoke", "user_id": "u_smoke_ok"}


@pytest.mark.asyncio
async def test_smoke_handler_accepts_dict_payload() -> None:
    """arq delivers a deserialized dict; the handler must accept it."""
    run_id = _create_queued_run("u_smoke_dict")

    result = await smoke(
        {"job_id": "arq-job-1"},
        {
            "workflow_type": "smoke",
            "user_id": "u_smoke_dict",
            "agent_run_id": run_id,
            "idempotency_key": "k",
        },
    )

    assert result == run_id
    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, run_id)
        assert run is not None
        assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_smoke_handler_missing_run_returns_marker() -> None:
    """A missing run id degrades gracefully instead of raising."""
    result = await smoke(
        {},
        SmokePayload(
            user_id="u_missing",
            agent_run_id="does_not_exist",
            idempotency_key="k",
        ),
    )
    assert result == "missing_run"


# ---------------------------------------------------------------------------
# 6: fail_run guard marks a run failed
# ---------------------------------------------------------------------------


def test_fail_run_marks_run_failed_with_sanitized_error() -> None:
    run_id = _create_queued_run("u_fail")

    fail_run(run_id, error="model call failed")

    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "model call failed"
        assert run.finished_at is not None


def test_fail_run_missing_run_is_noop() -> None:
    # Must not raise on a nonexistent run id.
    fail_run("does_not_exist", error="unreachable")
