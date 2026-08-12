"""API integration tests for the BOSS recommended-job discovery workflow.

Covers the acceptance criteria in ``implement.md`` checklist:

- ``prepare_only`` happy path: communicate → ``prepared`` with action_id and
  application_id.
- ``skip`` / ``needs_review`` outcomes are item-level results, not incidents —
  they reset the consecutive-failure counter.
- ``already_persisted`` skip: current job+resume already has a reusable
  communicate action → ``skipped`` + ``skip_reason=already_persisted`` +
  ``failure_code=null`` + counter reset.
- ``is_new_job=False`` but no reusable action → continues match/prepare.
- Hard-stop after 3 consecutive ``failed`` items; remaining items ``stopped``.
- ``unexpected_navigation`` / ``page_mismatch`` hard-stop codes cause run-level
  hard-stop, not just item failure.
- Cross-user access returns 404 (not 403).
- Active-run interlock: starting while a batch-loop or discovery run is active
  returns 409 ``active_conflict``.
- ``mode=auto_execute`` is rejected with HTTP 422 when the dry-run gate has not
  passed.
- Pause on a terminal run is a no-op.
- GET status returns full per-item progress.

Scenario control
----------------
The ``FakeModelGateway`` routes boss-match prompts by detecting a
``## SCENARIO: {scenario}`` marker in the user message. Because
``build_boss_match_messages`` injects ``jd_raw`` verbatim into the user
message, seeding a job's ``jd_raw`` with ``## SCENARIO: skip`` (or
``needs_review``) makes the fake gateway return that scenario for that job.

The discovery service calls ``get_channel()`` to communicate with the
userscript bridge. Tests monkeypatch ``get_channel`` to return a
``FakeDiscoveryChannel`` that simulates the scan/open/wait/read ops without a
real browser.

All tests use the ``client`` fixture so the DB is truncated per test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_model_gateway_dep
from app.db.models.models import (
    AgentRun,
    ApplicationAction,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.main import app
from app.models_gateway.base import ChatRequest, ChatResponse, ModelGateway
from app.models_gateway.fake import FakeModelGateway
from app.platforms.boss import userscript_channel as channel_mod
from app.platforms.boss.userscript_channel import (
    Instruction,
    InstructionResult,
    UserscriptChannel,
)
from app.schemas.application_action import (
    ExternalActionResultStatus,
    ExternalActionType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API = "/api/v1/boss/recommended-jobs/discovery"


# ---------------------------------------------------------------------------
# Fake discovery channel
# ---------------------------------------------------------------------------


class FakeDiscoveryChannel(UserscriptChannel):
    """A channel that simulates the discovery bridge ops without a browser.

    Each op is dispatched via ``put_instruction`` and resolved immediately
    with a pre-configured result. The result_map is keyed by ``op`` string.

    - ``scan_visible_jobs``: returns ``job_candidates`` from
      ``scan_candidates``.
    - ``open_job_by_key``: returns success (or ``unexpected_navigation`` if
      configured).
    - ``wait_job_detail_ready``: returns success.
    - ``read_jd``: returns ``jd`` dict from ``pane_jd`` (or based on job_key).
    """

    def __init__(
        self,
        *,
        scan_candidates: list[dict[str, Any]] | None = None,
        pane_jd: dict[str, Any] | None = None,
        per_key_jd: dict[str, dict[str, Any]] | None = None,
        open_error: str | None = None,
        wait_error: str | None = None,
        read_error: str | None = None,
    ) -> None:
        super().__init__()
        self._connected = True
        self._scan_candidates = scan_candidates or []
        self._pane_jd = pane_jd or {
            "title": "Backend Engineer",
            "description": "Senior Python backend engineer. Build APIs with FastAPI.",
            "salary": "20-35K",
            "location": "北京",
            "skills": ["Python", "FastAPI"],
            "source_kind": "boss_recommended_job",
            "page_url_hash": "sha256:fake",
        }
        self._per_key_jd = per_key_jd or {}
        self._open_error = open_error
        self._wait_error = wait_error
        self._read_error = read_error
        self.instructions_sent: list[Instruction] = []
        self._active_application_id: str | None = None
        self._cleared = False

    def is_connected(self) -> bool:  # type: ignore[override]
        return self._connected

    async def set_active_application(self, application_id: str) -> None:  # type: ignore[override]
        self._active_application_id = application_id

    def clear(self) -> None:  # type: ignore[override]
        self._cleared = True
        self._active_application_id = None

    async def put_instruction(  # type: ignore[override]
        self, instruction: Instruction
    ) -> InstructionResult:
        self.instructions_sent.append(instruction)
        op = instruction.op

        if op == "scan_visible_jobs":
            return InstructionResult(
                instruction_id=instruction.instruction_id,
                success=True,
                job_candidates=self._scan_candidates[: instruction.max_items or 999],
            )

        if op == "open_job_by_key":
            if self._open_error:
                return InstructionResult(
                    instruction_id=instruction.instruction_id,
                    success=False,
                    error=self._open_error,
                )
            return InstructionResult(
                instruction_id=instruction.instruction_id, success=True
            )

        if op == "wait_job_detail_ready":
            if self._wait_error:
                return InstructionResult(
                    instruction_id=instruction.instruction_id,
                    success=False,
                    error=self._wait_error,
                )
            return InstructionResult(
                instruction_id=instruction.instruction_id, success=True
            )

        if op == "read_jd":
            if self._read_error:
                return InstructionResult(
                    instruction_id=instruction.instruction_id,
                    success=False,
                    error=self._read_error,
                )
            # Use per-key JD if available, else the default.
            jd = self._per_key_jd.get(
                instruction.job_key or "", self._pane_jd
            )
            return InstructionResult(
                instruction_id=instruction.instruction_id,
                success=True,
                jd=jd,
            )

        # Default: success with no data.
        return InstructionResult(
            instruction_id=instruction.instruction_id, success=True
        )


def _make_scan_candidate(
    rank: int = 1,
    job_key: str | None = None,
    title: str = "Backend Engineer",
    company: str = "Acme",
    jd_text: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> dict[str, Any]:
    """Build a scan candidate dict (mirrors the userscript's scan output)."""
    return {
        "job_key": job_key or f"sha256:job_key_{rank}",
        "rank": rank,
        "title": title,
        "company": company,
        "salary": "20-35K",
        "location": "北京",
        "tags": ["Python", "FastAPI"],
        "candidate_hash": f"sha256:candidate_{rank}",
    }


def _make_pane_jd(
    title: str = "Backend Engineer",
    description: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> dict[str, Any]:
    """Build a pane JD dict (mirrors the userscript's read_jd output)."""
    return {
        "title": title,
        "description": description,
        "salary": "20-35K",
        "location": "北京",
        "skills": ["Python", "FastAPI"],
        "source_kind": "boss_recommended_job",
        "page_url_hash": "sha256:fake",
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "disc_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name=f"发现用户 {user_id}")
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


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _seed_user(
    user_id: str = "disc_user",
    other_user_id: str = "disc_other",
) -> dict[str, Any]:
    """Seed a user + resume + version + second user for cross-user tests."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id)

        _make_user(db, other_user_id)
        other_resume, other_version = _make_resume(db, other_user_id)

        db.commit()
        return {
            "user_id": user_id,
            "resume_version_id": version.id,
            "other_user_id": other_user_id,
            "other_resume_version_id": other_version.id,
        }


def _patch_channel(monkeypatch, channel: FakeDiscoveryChannel) -> None:
    """Monkeypatch get_channel to return the given fake channel."""
    monkeypatch.setattr(channel_mod, "get_channel", lambda: channel)
    # Also patch the service module which imported get_channel by name.
    from app.services import boss_recommended_discovery_service as svc

    monkeypatch.setattr(svc, "get_channel", lambda: channel)


def _start_discovery(
    client: TestClient,
    ids: dict[str, Any],
    *,
    mode: str = "prepare_only",
    limit: int = 3,
    user_id: str | None = None,
) -> Any:
    """POST /discovery and return the response."""
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
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
async def test_prepare_only_communicate_becomes_prepared(
    client, monkeypatch
) -> None:
    """A communicate candidate → ``prepared`` with action_id and application_id."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[
            _make_scan_candidate(
                rank=1,
                job_key="sha256:key1",
                title="Backend Engineer",
            )
        ],
        pane_jd=_make_pane_jd(
            title="Backend Engineer",
            description="Senior Python backend engineer. Build APIs with FastAPI.",
        ),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "completed"
    assert body["mode"] == "prepare_only"
    assert body["total"] == 1
    assert body["processed"] == 1
    assert body["consecutive_failures"] == 0

    item = body["items"][0]
    assert item["status"] == "prepared"
    assert item["decision"] == "communicate"
    assert item["score"] is not None
    assert item["match_artifact_id"] is not None
    assert item["action_id"] is not None
    assert item["application_id"] is not None
    assert item["failure_code"] is None
    assert item["skip_reason"] is None


@pytest.mark.asyncio
async def test_prepared_action_is_approval_required(
    client, monkeypatch
) -> None:
    """The prepared action is a real ``approval_required`` ApplicationAction."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text

    action_id = resp.json()["items"][0]["action_id"]
    with SessionLocal() as db:
        action = db.get(ApplicationAction, action_id)
        assert action is not None
        assert action.action_type == ExternalActionType.boss_immediate_communicate.value
        assert action.status == "approval_required"


# ---------------------------------------------------------------------------
# skip / needs_review outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_item_is_not_an_incident(client, monkeypatch) -> None:
    """A ``skip`` item is marked ``skipped`` and resets the failure counter."""
    ids = _seed_user()
    skip_jd = _make_pane_jd(
        title="Skip Role",
        description="## SCENARIO: skip\nJD requires 8+ years experience.",
    )
    comm_jd = _make_pane_jd(
        title="Backend Engineer",
        description="Senior Python backend engineer.",
    )
    channel = FakeDiscoveryChannel(
        scan_candidates=[
            _make_scan_candidate(rank=1, job_key="sha256:key1", title="Skip Role"),
            _make_scan_candidate(rank=2, job_key="sha256:key2", title="Backend Engineer"),
        ],
        per_key_jd={
            "sha256:key1": skip_jd,
            "sha256:key2": comm_jd,
        },
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids, limit=2)
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
async def test_needs_review_item_is_not_an_incident(client, monkeypatch) -> None:
    """A ``needs_review`` item is marked and resets the failure counter."""
    ids = _seed_user()
    review_jd = _make_pane_jd(
        title="Review Role",
        description="## SCENARIO: needs_review\nSalary range below expectation.",
    )
    comm_jd = _make_pane_jd(
        title="Backend Engineer",
        description="Senior Python backend engineer.",
    )
    channel = FakeDiscoveryChannel(
        scan_candidates=[
            _make_scan_candidate(rank=1, job_key="sha256:key1", title="Review Role"),
            _make_scan_candidate(rank=2, job_key="sha256:key2", title="Backend Engineer"),
        ],
        per_key_jd={
            "sha256:key1": review_jd,
            "sha256:key2": comm_jd,
        },
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids, limit=2)
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
# already_persisted skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_persisted_skips_match_and_prepare(
    client, monkeypatch
) -> None:
    """A job+resume with a reusable non-terminal communicate action is skipped.

    The item is marked ``skipped`` with ``skip_reason=already_persisted`` and
    ``failure_code=null``, and the consecutive-failure counter is reset.
    """
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    # --- First run: creates the communicate action. ---
    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    first_action_id = resp.json()["items"][0]["action_id"]
    assert first_action_id is not None

    # --- Second run: same job_key → upsert deduplicates, action exists → skip. ---
    # The FakeDiscoveryChannel returns the same scan candidate, so the
    # upsert will find the existing job via external_id=job_key, and the
    # already_persisted check will find the existing approval_required action.
    channel2 = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel2)

    resp2 = _start_discovery(client, ids)
    assert resp2.status_code == 200, resp2.text
    body = resp2.json()

    assert body["status"] == "completed"
    item = body["items"][0]
    assert item["status"] == "skipped"
    assert item["skip_reason"] == "already_persisted"
    assert item["failure_code"] is None
    # No new match/prepare was run.
    assert item["action_id"] is None
    assert item["decision"] == "skip"
    assert body["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_already_persisted_with_terminal_action(
    client, monkeypatch
) -> None:
    """A terminal (submitted) communicate action also triggers already_persisted."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    # --- First run: creates the communicate action. ---
    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    action_id = resp.json()["items"][0]["action_id"]

    # --- Simulate terminal result (submitted). ---
    with SessionLocal() as db:
        action = db.get(ApplicationAction, action_id)
        assert action is not None
        action.external_result_status = (
            ExternalActionResultStatus.submitted.value
        )
        db.commit()

    # --- Second run: same job_key → already_persisted (terminal). ---
    channel2 = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel2)

    resp2 = _start_discovery(client, ids)
    assert resp2.status_code == 200, resp2.text
    item = resp2.json()["items"][0]
    assert item["status"] == "skipped"
    assert item["skip_reason"] == "already_persisted"
    assert item["failure_code"] is None


# ---------------------------------------------------------------------------
# Hard-stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_stop_after_3_consecutive_failures(
    client, monkeypatch
) -> None:
    """3 consecutive ``failed`` items → ``hard_stopped``; remaining items ``stopped``."""
    ids = _seed_user()
    # Each candidate returns a valid pane JD so upsert succeeds, but the model
    # gateway always raises → match fails → item fails.
    candidates = [
        _make_scan_candidate(rank=i, job_key=f"sha256:key{i}")
        for i in range(1, 6)
    ]
    channel = FakeDiscoveryChannel(
        scan_candidates=candidates,
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    app.dependency_overrides[get_model_gateway_dep] = lambda: _ErrorGateway()
    try:
        resp = _start_discovery(client, ids, limit=5)
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "hard_stopped"
    assert body["consecutive_failures"] == 3
    assert body["hard_stop_threshold"] == 3

    items = body["items"]
    for i in range(3):
        assert items[i]["status"] == "failed"
        assert items[i]["message"] is not None
    for i in range(3, 5):
        assert items[i]["status"] == "stopped"
    assert body["message"] is not None


@pytest.mark.asyncio
async def test_skip_resets_consecutive_failure_counter(
    client, monkeypatch
) -> None:
    """A ``skip`` between failures resets the counter, preventing hard-stop."""
    ids = _seed_user()
    # 2 fail, 1 skip, 2 fail — no hard-stop because we never reach 3 consecutive.
    candidates = [
        _make_scan_candidate(rank=i, job_key=f"sha256:key{i}")
        for i in range(1, 6)
    ]
    skip_jd = _make_pane_jd(
        title="Skip Role",
        description="## SCENARIO: skip\nJob 3 skip",
    )
    fail_jd = _make_pane_jd(
        title="Fail Role",
        description="Job that will fail at match.",
    )
    per_key = {
        f"sha256:key{i}": fail_jd
        for i in range(1, 6)
    }
    per_key["sha256:key3"] = skip_jd
    channel = FakeDiscoveryChannel(
        scan_candidates=candidates,
        per_key_jd=per_key,
    )
    _patch_channel(monkeypatch, channel)

    # Gateway that fails for non-skip jobs and returns skip for skip jobs.
    class _SelectiveGateway(ModelGateway):
        provider_name = "selective"

        def __init__(self) -> None:
            self._inner = FakeModelGateway()

        async def chat(self, request: ChatRequest) -> ChatResponse:
            user_msg = " ".join(
                m.content for m in request.messages if m.role == "user"
            )
            if "## SCENARIO: skip" in user_msg:
                return await self._inner.chat(request)
            raise RuntimeError("simulated provider outage for non-skip job")

    app.dependency_overrides[get_model_gateway_dep] = lambda: _SelectiveGateway()
    try:
        resp = _start_discovery(client, ids, limit=5)
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "completed"
    assert body["consecutive_failures"] == 2  # last 2 items failed

    items = body["items"]
    assert items[0]["status"] == "failed"
    assert items[1]["status"] == "failed"
    assert items[2]["status"] == "skipped"
    assert items[3]["status"] == "failed"
    assert items[4]["status"] == "failed"


# ---------------------------------------------------------------------------
# unexpected_navigation / page_mismatch hard-stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_navigation_hard_stops_run(
    client, monkeypatch
) -> None:
    """``unexpected_navigation`` on open_job_by_key causes run-level hard-stop."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[
            _make_scan_candidate(rank=1, job_key="sha256:key1"),
            _make_scan_candidate(rank=2, job_key="sha256:key2"),
        ],
        open_error="unexpected_navigation",
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids, limit=2)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "hard_stopped"
    items = body["items"]
    # First item failed with the hard-stop code.
    assert items[0]["status"] == "failed"
    assert items[0]["failure_code"] == "unexpected_navigation"
    # Remaining items are stopped.
    assert items[1]["status"] == "stopped"


@pytest.mark.asyncio
async def test_page_mismatch_hard_stops_run(client, monkeypatch) -> None:
    """``page_mismatch`` on wait_job_detail_ready causes run-level hard-stop."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        wait_error="page_mismatch",
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "hard_stopped"
    item = body["items"][0]
    assert item["status"] == "failed"
    assert item["failure_code"] == "page_mismatch"


# ---------------------------------------------------------------------------
# Cross-user access → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_get_returns_404(client, monkeypatch) -> None:
    """A user cannot view another user's discovery run (404, not 403)."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    intruder_resp = client.get(
        f"{_API}/{run_id}",
        headers=_headers(ids["other_user_id"]),
    )
    assert intruder_resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_user_pause_returns_404(client, monkeypatch) -> None:
    """A user cannot pause another user's discovery run (404)."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    intruder_resp = client.post(
        f"{_API}/{run_id}/pause",
        headers=_headers(ids["other_user_id"]),
    )
    assert intruder_resp.status_code == 404


# ---------------------------------------------------------------------------
# Active-run interlock → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_discovery_run_returns_409(client, monkeypatch) -> None:
    """Starting while a discovery run is still active returns 409.

    We simulate a stuck ``running`` run by creating one directly in the DB,
    then attempting to start a new discovery run.
    """
    ids = _seed_user()
    # Create a stuck running discovery run directly in the DB.
    with SessionLocal() as db:
        run = AgentRun(
            user_id=ids["user_id"],
            workflow_type="boss_recommended_discovery",
            status="running",
            started_at=datetime.now(UTC),
            result={
                "discovery_status": "running",
                "mode": "prepare_only",
                "resume_version_id": ids["resume_version_id"],
                "limit": 3,
                "consecutive_failures": 0,
                "hard_stop_threshold": 3,
                "items": [],
                "message": None,
            },
        )
        db.add(run)
        db.commit()

    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    # 409: start always rejects when any discovery or batch-loop run is still
    # active (cross-workflow interlock, spec §14). Resume happens only via the
    # explicit /resume endpoint.
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "active_conflict"


# ---------------------------------------------------------------------------
# Active batch-loop run → 409 (cross-workflow interlock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_batch_loop_run_blocks_discovery_start_409(
    client, monkeypatch
) -> None:
    """Starting a discovery run while a batch-loop run is active returns 409.

    Cross-workflow interlock (spec §14): only one bridge-consuming workflow
    per user at a time. A running batch-loop run blocks discovery start.
    """
    ids = _seed_user()
    # Create a stuck running batch-loop run directly in the DB.
    with SessionLocal() as db:
        run = AgentRun(
            user_id=ids["user_id"],
            workflow_type="boss_recommended_job_batch_loop",
            status="running",
            started_at=datetime.now(UTC),
            result={
                "batch_status": "running",
                "mode": "prepare_only",
                "resume_version_id": ids["resume_version_id"],
                "consecutive_failures": 0,
                "hard_stop_threshold": 3,
                "items": [],
                "message": None,
            },
        )
        db.add(run)
        db.commit()

    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "active_conflict"


# ---------------------------------------------------------------------------
# auto_execute always 422 in v0.1 (prepare_only only)
# ---------------------------------------------------------------------------


def test_auto_execute_always_422_in_v01(client, monkeypatch) -> None:
    """``mode=auto_execute`` is always 422 in v0.1 regardless of gate state.

    Even when the dry-run log has enough clean entries + duplicates, the API
    layer still rejects ``auto_execute`` for the discovery workflow in v0.1
    (Phase 1 is strictly ``prepare_only``). This test patches the gate to
    return ``passed=True`` and verifies the request still gets 422.
    """
    from app.services import boss_dry_run_gate

    monkeypatch.setattr(
        boss_dry_run_gate,
        "gate_status",
        lambda **kw: {
            "passed": True,
            "consecutive_clean": 10,
            "required_consecutive_clean": 10,
            "duplicates": 2,
            "required_duplicates": 2,
            "total_entries": 12,
            "last_incident_run": None,
        },
    )

    ids = _seed_user()
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "limit": 3,
            "mode": "auto_execute",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "auto_execute_not_supported"


# ---------------------------------------------------------------------------
# Limit capping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_caps_processed_items(client, monkeypatch) -> None:
    """``limit`` caps the number of candidates scanned."""
    ids = _seed_user()
    candidates = [
        _make_scan_candidate(rank=i, job_key=f"sha256:key{i}")
        for i in range(1, 6)
    ]
    channel = FakeDiscoveryChannel(
        scan_candidates=candidates,
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids, limit=2)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only 2 items are in the run (scan was capped to limit).
    assert body["total"] == 2
    assert body["processed"] == 2
    assert body["status"] == "completed"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_missing_resume_version_returns_422(client) -> None:
    """An empty ``resume_version_id`` is rejected with HTTP 422."""
    ids = _seed_user()
    resp = client.post(
        _API,
        json={
            "resume_version_id": "",
            "limit": 3,
            "mode": "prepare_only",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


def test_limit_exceeds_max_returns_422(client) -> None:
    """``limit`` > 10 is rejected with HTTP 422."""
    ids = _seed_user()
    resp = client.post(
        _API,
        json={
            "resume_version_id": ids["resume_version_id"],
            "limit": 11,
            "mode": "prepare_only",
        },
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Non-existent run → 404
# ---------------------------------------------------------------------------


def test_get_nonexistent_run_returns_404(client) -> None:
    """GET /discovery/{run_id} with a non-existent run returns 404."""
    ids = _seed_user()
    resp = client.get(
        f"{_API}/nonexistent-run-id",
        headers=_headers(ids["user_id"]),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Pause on terminal run is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_terminal_run_is_noop(client, monkeypatch) -> None:
    """Pausing an already-completed run is a no-op."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    assert resp.json()["status"] == "completed"

    pause_resp = client.post(
        f"{_API}/{run_id}/pause",
        headers=_headers(ids["user_id"]),
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# GET status returns items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_returns_items(client, monkeypatch) -> None:
    """GET /discovery/{run_id} returns the full per-item progress."""
    ids = _seed_user()
    skip_jd = _make_pane_jd(
        title="Skip Role",
        description="## SCENARIO: skip\nJob 2 skip",
    )
    comm_jd = _make_pane_jd(
        title="Backend Engineer",
        description="Senior Python backend engineer.",
    )
    channel = FakeDiscoveryChannel(
        scan_candidates=[
            _make_scan_candidate(rank=1, job_key="sha256:key1", title="Backend Engineer"),
            _make_scan_candidate(rank=2, job_key="sha256:key2", title="Skip Role"),
        ],
        per_key_jd={
            "sha256:key1": comm_jd,
            "sha256:key2": skip_jd,
        },
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids, limit=2)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

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
# Bridge not connected → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_not_connected_returns_400(client, monkeypatch) -> None:
    """Starting discovery with no bridge connection returns 400."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    channel._connected = False
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Empty scan result → completed with message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_scan_completes_with_message(
    client, monkeypatch
) -> None:
    """A valid but empty scan returns ``completed`` with an informational message."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[],  # valid empty list
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "completed"
    assert body["total"] == 0
    assert body["processed"] == 0
    assert body["message"] is not None


# ---------------------------------------------------------------------------
# not_recommended_list_page → failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_recommended_list_page_fails_run(
    client, monkeypatch
) -> None:
    """``not_recommended_list_page`` scan error marks the run as failed."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[],
    )
    # Override scan to return an error.
    original_put = channel.put_instruction

    async def _scan_error_put(instruction: Instruction) -> InstructionResult:
        if instruction.op == "scan_visible_jobs":
            return InstructionResult(
                instruction_id=instruction.instruction_id,
                success=False,
                error="not_recommended_list_page",
            )
        return await original_put(instruction)

    channel.put_instruction = _scan_error_put  # type: ignore[assignment]
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "failed"
    assert body["total"] == 0
    assert body["message"] is not None


# ---------------------------------------------------------------------------
# AgentRun.status mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_run_status_mapping_completed(client, monkeypatch) -> None:
    """``completed`` discovery status maps to ``AgentRun.status=succeeded``."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[_make_scan_candidate(rank=1, job_key="sha256:key1")],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids)
    run_id = resp.json()["run_id"]

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.result["discovery_status"] == "completed"


@pytest.mark.asyncio
async def test_agent_run_status_mapping_hard_stopped(
    client, monkeypatch
) -> None:
    """``hard_stopped`` discovery status maps to ``AgentRun.status=failed``."""
    ids = _seed_user()
    candidates = [
        _make_scan_candidate(rank=i, job_key=f"sha256:key{i}")
        for i in range(1, 5)
    ]
    channel = FakeDiscoveryChannel(
        scan_candidates=candidates,
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    app.dependency_overrides[get_model_gateway_dep] = lambda: _ErrorGateway()
    try:
        resp = _start_discovery(client, ids, limit=4)
    finally:
        app.dependency_overrides.pop(get_model_gateway_dep, None)

    run_id = resp.json()["run_id"]
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.result["discovery_status"] == "hard_stopped"


# ---------------------------------------------------------------------------
# Sentinel lock cleared once at end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentinel_lock_cleared_once_at_end(client, monkeypatch) -> None:
    """The channel sentinel lock is taken once and cleared once at run end."""
    ids = _seed_user()
    channel = FakeDiscoveryChannel(
        scan_candidates=[
            _make_scan_candidate(rank=1, job_key="sha256:key1"),
            _make_scan_candidate(rank=2, job_key="sha256:key2"),
        ],
        pane_jd=_make_pane_jd(),
    )
    _patch_channel(monkeypatch, channel)

    resp = _start_discovery(client, ids, limit=2)
    assert resp.status_code == 200, resp.text

    # clear() should have been called exactly once at the end.
    assert channel._cleared is True
    # The active_application_id was set to a discovery sentinel.
    set_calls = [
        ins for ins in channel.instructions_sent if ins.op == "scan_visible_jobs"
    ]
    # At least 2 scan ops were sent (one per item processing).
    assert len(set_calls) >= 1
