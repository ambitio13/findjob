"""API integration tests for the BOSS recommended-job batch-loop workflow.

Covers the acceptance criteria in ``prd.md``:

- ``prepare_only`` happy path: communicate → ``prepared`` with action_id and
  application_id.
- ``skip`` / ``needs_review`` outcomes are item-level results, not incidents —
  they reset the consecutive-failure counter.
- Hard-stop after 3 consecutive ``failed`` items; remaining items marked
  ``stopped``.
- Pause / resume functionality.
- Cross-user access returns 404 (not 403).
- ``mode=auto_execute`` is rejected with HTTP 422 when the dry-run gate has not
  passed.

Scenario control
----------------
The ``FakeModelGateway`` routes boss-match prompts by detecting a
``## SCENARIO: {scenario}`` marker in the user message. Because
``build_boss_match_messages`` injects ``jd_raw`` verbatim into the user
message, seeding a job's ``jd_raw`` with ``## SCENARIO: skip`` (or
``needs_review``) makes the fake gateway return that scenario for that job
— no custom gateway needed.

All tests use the ``client`` fixture so the DB is truncated per test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_model_gateway_dep
from app.db.models.models import (
    ApplicationAction,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.main import app
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.models_gateway.fake import FakeModelGateway
from app.services import boss_dry_run_gate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API = "/api/v1/boss/recommended-jobs/batch-loop"

# ---------------------------------------------------------------------------
# DB helpers (mirror test_boss_communicate_service.py shape)
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "batch_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name=f"批量用户 {user_id}")
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


def _seed_jobs(
    user_id: str = "batch_user",
    *,
    jd_texts: list[str] | None = None,
    other_user_id: str = "batch_other",
) -> dict[str, Any]:
    """Seed a user + resume + version + multiple jobs, returning their IDs.

    ``jd_texts`` controls the per-job scenario: when a text contains
    ``## SCENARIO: skip`` or ``## SCENARIO: needs_review`` the fake gateway
    returns that decision. Otherwise the default ``communicate`` scenario is
    used.
    """
    if jd_texts is None:
        jd_texts = ["Senior Python backend engineer. Build APIs with FastAPI."]

    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id)

        job_ids: list[str] = []
        for i, jd in enumerate(jd_texts):
            job = _make_job(db, user_id, jd_raw=jd, external_id=f"sha256:url_{i}")
            job_ids.append(job.id)

        # Seed a second user for cross-user tests.
        _make_user(db, other_user_id)
        other_resume, other_version = _make_resume(db, other_user_id)
        other_job = _make_job(db, other_user_id)

        db.commit()
        return {
            "user_id": user_id,
            "resume_version_id": version.id,
            "job_ids": job_ids,
            "other_user_id": other_user_id,
            "other_resume_version_id": other_version.id,
            "other_job_id": other_job.id,
        }


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _start_batch(
    client: TestClient,
    ids: dict[str, Any],
    *,
    mode: str = "prepare_only",
    limit: int = 10,
    user_id: str | None = None,
    job_ids: list[str] | None = None,
) -> Any:
    """POST /batch-loop and return the parsed JSON response."""
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "job_ids": job_ids or ids["job_ids"],
            "limit": limit,
            "mode": mode,
        },
        headers=_headers(user_id or ids["user_id"]),
    )
    return resp


# ---------------------------------------------------------------------------
# Stub gateways
# ---------------------------------------------------------------------------


class _ErrorGateway(ModelGateway):
    """Gateway that always raises — used to trigger ``failed`` items."""

    provider_name = "error"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("simulated provider outage")


# ---------------------------------------------------------------------------
# prepare_only — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_only_communicate_items_become_prepared(client) -> None:
    """Each communicate job → ``prepared`` with action_id and application_id."""
    ids = _seed_jobs(
        jd_texts=[
            "Senior Python backend engineer. Build APIs with FastAPI.",
            "Another Python role. Django and PostgreSQL.",
        ],
    )

    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "completed"
    assert body["mode"] == "prepare_only"
    assert body["total"] == 2
    assert body["processed"] == 2
    assert body["consecutive_failures"] == 0

    for item in body["items"]:
        assert item["status"] == "prepared"
        assert item["decision"] == "communicate"
        assert item["score"] is not None
        assert item["match_artifact_id"] is not None
        assert item["action_id"] is not None
        assert item["application_id"] is not None
        assert item["error"] is None


@pytest.mark.asyncio
async def test_prepare_only_persists_approval_required_actions(client) -> None:
    """The prepared actions are real ``approval_required`` ApplicationActions."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text

    action_id = resp.json()["items"][0]["action_id"]
    with SessionLocal() as db:
        action = db.get(ApplicationAction, action_id)
        assert action is not None
        assert action.action_type == "boss_immediate_communicate"
        assert action.status == "approval_required"


# ---------------------------------------------------------------------------
# skip / needs_review outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_item_is_not_an_incident(client) -> None:
    """A ``skip`` item is marked ``skipped`` and resets the failure counter."""
    ids = _seed_jobs(
        jd_texts=[
            "## SCENARIO: skip\nJD requires 8+ years experience.",
            "Senior Python backend engineer.",
        ],
    )

    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "completed"
    items = body["items"]
    assert items[0]["status"] == "skipped"
    assert items[0]["decision"] == "skip"
    assert items[0]["action_id"] is None
    assert items[1]["status"] == "prepared"
    assert body["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_needs_review_item_is_not_an_incident(client) -> None:
    """A ``needs_review`` item is marked and resets the failure counter."""
    ids = _seed_jobs(
        jd_texts=[
            "## SCENARIO: needs_review\nSalary range below expectation.",
            "Senior Python backend engineer.",
        ],
    )

    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "completed"
    items = body["items"]
    assert items[0]["status"] == "needs_review"
    assert items[0]["decision"] == "needs_review"
    assert items[0]["action_id"] is None
    assert items[1]["status"] == "prepared"
    assert body["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Hard-stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_stop_after_3_consecutive_failures(client) -> None:
    """3 consecutive ``failed`` items → ``hard_stopped``; remaining items ``stopped``."""
    ids = _seed_jobs(
        jd_texts=[
            "Job 1",
            "Job 2",
            "Job 3",
            "Job 4",
            "Job 5",
        ],
    )

    # Override the model gateway dependency to always raise, so every item fails.
    app.dependency_overrides[get_model_gateway_dep] = lambda: _ErrorGateway()
    try:
        resp = _start_batch(client, ids)
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "hard_stopped"
    assert body["consecutive_failures"] == 3
    assert body["hard_stop_threshold"] == 3

    items = body["items"]
    # First 3 items should be ``failed``.
    for i in range(3):
        assert items[i]["status"] == "failed"
        assert items[i]["error"] is not None
    # Remaining items should be ``stopped``.
    for i in range(3, 5):
        assert items[i]["status"] == "stopped"
    assert body["message"] is not None


@pytest.mark.asyncio
async def test_skip_resets_consecutive_failure_counter(client) -> None:
    """A ``skip`` between failures resets the counter, preventing hard-stop."""
    # 2 failures, then a skip (resets counter), then 2 more failures — no
    # hard-stop because we never reach 3 consecutive.
    ids = _seed_jobs(
        jd_texts=[
            "Job 1",  # will fail
            "Job 2",  # will fail
            "## SCENARIO: skip\nJob 3 skip",  # resets counter
            "Job 4",  # will fail
            "Job 5",  # will fail
        ],
    )

    # Use a gateway that fails for non-skip jobs and returns skip for skip jobs.
    class _SelectiveGateway(ModelGateway):
        provider_name = "selective"

        def __init__(self) -> None:
            self._inner = FakeModelGateway()

        async def chat(self, request: ChatRequest) -> ChatResponse:
            # The fake gateway detects scenario from the user message content.
            # Jobs without a scenario marker get "communicate" by default.
            # We make non-skip jobs fail by raising.
            user_msg = " ".join(
                m.content for m in request.messages if m.role == "user"
            )
            if "## SCENARIO: skip" in user_msg:
                return await self._inner.chat(request)
            raise RuntimeError("simulated provider outage for non-skip job")

    app.dependency_overrides[get_model_gateway_dep] = lambda: _SelectiveGateway()
    try:
        resp = _start_batch(client, ids)
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Batch should complete (not hard-stopped) because the skip reset the
    # counter and we never reached 3 consecutive failures.
    assert body["status"] == "completed"
    assert body["consecutive_failures"] == 2  # last 2 items failed

    items = body["items"]
    assert items[0]["status"] == "failed"
    assert items[1]["status"] == "failed"
    assert items[2]["status"] == "skipped"
    assert items[3]["status"] == "failed"
    assert items[4]["status"] == "failed"


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


def test_pause_terminal_run_is_noop(client) -> None:
    """Pausing an already-completed run is a no-op."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    assert resp.json()["status"] == "completed"

    # Pause the completed run.
    pause_resp = client.post(
        f"{_API}/{run_id}/pause",
        headers=_headers(ids["user_id"]),
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_get_batch_status_returns_items(client) -> None:
    """GET /batch-loop/{run_id} returns the full per-item progress."""
    ids = _seed_jobs(
        jd_texts=[
            "Senior Python backend engineer.",
            "## SCENARIO: skip\nJob 2 skip",
        ],
    )
    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # Poll the status.
    status_resp = client.get(
        f"{_API}/{run_id}",
        headers=_headers(ids["user_id"]),
    )
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["run_id"] == run_id
    assert body["status"] == "completed"
    assert len(body["items"]) == 2
    assert body["items"][0]["status"] == "prepared"
    assert body["items"][1]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Cross-user access → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_get_returns_404(client) -> None:
    """A user cannot view another user's batch run (404, not 403)."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    intruder_resp = client.get(
        f"{_API}/{run_id}",
        headers=_headers(ids["other_user_id"]),
    )
    assert intruder_resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_user_pause_returns_404(client) -> None:
    """A user cannot pause another user's batch run (404)."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = _start_batch(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    intruder_resp = client.post(
        f"{_API}/{run_id}/pause",
        headers=_headers(ids["other_user_id"]),
    )
    assert intruder_resp.status_code == 404


# ---------------------------------------------------------------------------
# auto_execute rejected (422) when dry-run gate not passed
# ---------------------------------------------------------------------------


def test_auto_execute_rejected_when_gate_not_passed(client, monkeypatch) -> None:
    """``mode=auto_execute`` is rejected with HTTP 422 when the gate hasn't passed.

    The real dry-run log has only 1 entry (well below the 10-consecutive-clean
    + 2-duplicates threshold), so the gate is not passed. We also patch the
    gate path to a temp file to make the test fully deterministic.
    """
    import tempfile
    from pathlib import Path

    # Use an empty temp file so the gate is guaranteed not passed.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("")
        temp_path = Path(f.name)

    monkeypatch.setattr(boss_dry_run_gate, "_DRY_RUN_LOG_PATH", temp_path)

    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "job_ids": ids["job_ids"],
            "limit": 10,
            "mode": "auto_execute",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "auto_execute_gate_not_passed"


def test_auto_execute_allowed_when_gate_passed(client, monkeypatch) -> None:
    """When the dry-run gate passes, ``auto_execute`` is accepted (not 422).

    We patch the gate path to a temp file with 10 consecutive clean entries
    and 2 duplicate detections, then verify the endpoint does not return 422.
    The batch itself still runs in ``prepare_only`` semantics internally (the
    service does not auto-approve/execute), but the gate no longer blocks.
    """
    import tempfile
    from pathlib import Path

    lines: list[str] = []
    # 8 clean entries with 2 duplicate detections.
    for i in range(1, 9):
        result = "duplicate_detected" if i <= 2 else "succeeded"
        lines.append(
            json.dumps(
                {
                    "run": i,
                    "date": "2026-08-12",
                    "operator": "coldnight",
                    "match_decision": "communicate",
                    "incident": False,
                    "read_communication_result": result,
                    "anomalies": "无",
                },
                ensure_ascii=False,
            )
        )
    # 2 more clean entries to reach 10 consecutive clean.
    for i in range(9, 11):
        lines.append(
            json.dumps(
                {
                    "run": i,
                    "date": "2026-08-12",
                    "operator": "coldnight",
                    "match_decision": "communicate",
                    "incident": False,
                    "read_communication_result": "succeeded",
                    "anomalies": "无",
                },
                ensure_ascii=False,
            )
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        f.write("\n".join(lines) + "\n")
        temp_path = Path(f.name)

    monkeypatch.setattr(boss_dry_run_gate, "_DRY_RUN_LOG_PATH", temp_path)

    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "job_ids": ids["job_ids"],
            "limit": 10,
            "mode": "auto_execute",
        },
        headers=_headers(ids["user_id"]),
    )
    # Should NOT be 422 — the gate passed. The batch runs and returns 200.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "auto_execute"


# ---------------------------------------------------------------------------
# Limit capping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_caps_processed_items(client) -> None:
    """``limit`` caps the number of job_ids processed."""
    ids = _seed_jobs(
        jd_texts=["Job 1", "Job 2", "Job 3", "Job 4", "Job 5"],
    )

    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "job_ids": ids["job_ids"],
            "limit": 2,
            "mode": "prepare_only",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only 2 items are in the batch (job_ids was truncated to limit).
    assert body["total"] == 2
    assert body["processed"] == 2
    assert body["status"] == "completed"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_empty_job_ids_returns_422(client) -> None:
    """An empty ``job_ids`` list is rejected with HTTP 422 (min_length=1)."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "job_ids": [],
            "limit": 10,
            "mode": "prepare_only",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


def test_missing_resume_version_returns_422(client) -> None:
    """An empty ``resume_version_id`` is rejected with HTTP 422."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = client.post(
        _API,
        json={
            "resume_version_id": "",
            "job_ids": ids["job_ids"],
            "limit": 10,
            "mode": "prepare_only",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Non-existent run → 404
# ---------------------------------------------------------------------------


def test_get_nonexistent_run_returns_404(client) -> None:
    """GET /batch-loop/{run_id} with a non-existent run returns 404."""
    ids = _seed_jobs(jd_texts=["Senior Python backend engineer."])
    resp = client.get(
        f"{_API}/nonexistent-run-id",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 404
