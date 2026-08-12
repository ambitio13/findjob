"""BOSS recommended-job discovery service.

This service implements the **zero-navigation** serial discovery pipeline for
the BOSS recommended-job list page (``/web/geek/jobs``):

1. **Scan** — dispatch ``scan_visible_jobs`` via the userscript bridge to
   collect visible card candidates (``job_key`` + sanitized metadata).
2. **For each candidate** (up to ``limit``):
   a. **Open** — ``open_job_by_key`` clicks the card root; the right-hand pane
      switches in place (no navigation).
   b. **Wait** — ``wait_job_detail_ready`` confirms the pane title matches.
   c. **Read** — ``read_jd`` with the ``boss_recommended_pane_v1`` profile.
   d. **Upsert** — ``upsert_job_from_browser_jd`` with
      ``external_id_override=job_key``.
   e. **Skip check** — if the current job + resume already has a reusable
      communicate action (non-terminal or terminal), mark
      ``skipped(already_persisted)`` and continue.
   f. **Match** — ``run_boss_match_decision``.
   g. **Prepare** — when ``communicate``, ``prepare_communicate_action`` creates
      an ``approval_required`` action.
3. **Hard-stop** after 3 consecutive ``failed`` items (spec §14).

Phase 1 is ``prepare_only``: the pipeline never auto-approves or
auto-executes. ``auto_execute`` is rejected at the API layer via the dry-run
gate.

Safety invariants (mirrors ``evolution-contracts.md`` §1, §14):

- The discovery pipeline never auto-approves or auto-executes.
- Cross-user access returns 404 (not 403).
- Serial item processing only — no concurrent bridge instructions.
- ``needs_review`` / ``skipped`` (including ``already_persisted``) are item
  outcomes, not incidents — they reset the consecutive-failure counter.
- ``skip_reason`` is business metadata, not ``failure_code``.
- The channel sentinel lock (``discovery:<run_id>``) is held for the entire
  run; ``channel.clear()`` is NOT called between item ops.
- No raw URL, raw HTML, cookies, tokens, contact names, or chat content are
  persisted or logged.

Per-item progress is stored in ``AgentRun.result["items"]`` as a list of dicts
(same pattern as ``boss_batch_loop_service``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.logging import get_logger
from app.db.models.models import (
    AgentRun as AgentRunModel,
)
from app.db.models.models import (
    UserProfile,
)
from app.db.repositories import (
    agent_run_repo,
    application_action_repo,
    application_repo,
)
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway
from app.platforms.boss.userscript_channel import (
    InstructionResult,
    UserscriptChannel,
    get_channel,
    make_instruction,
)
from app.schemas.boss_match_decision import MatchDecision
from app.schemas.boss_recommended_discovery import (
    DiscoveryItemOut,
    DiscoveryItemStatus,
    DiscoveryRunStatus,
    DiscoveryStatusOut,
)
from app.services.boss_communicate_service import prepare_communicate_action
from app.services.boss_match_service import run_boss_match_decision
from app.services.boss_recommended_job_service import (
    is_jd_too_sparse,
    upsert_job_from_browser_jd,
)

_log = get_logger("app.services.boss_recommended_discovery_service")

#: The workflow type stored on the discovery ``AgentRun``.
WORKFLOW_TYPE = "boss_recommended_discovery"

#: Consecutive-failure threshold (spec §14). 3 consecutive ``failed`` items
#: stop the run and mark remaining items as ``stopped``.
_HARD_STOP_THRESHOLD = 3

#: Batch-loop workflow type — used for cross-workflow active-run interlock.
_BATCH_LOOP_WORKFLOW_TYPE = "boss_recommended_job_batch_loop"

#: Pane profile for the in-place detail pane on the recommended list page.
_PANE_PROFILE = "boss_recommended_pane_v1"


def _not_found(detail: str) -> HTTPException:
    """Return an HTTPException for 404 responses (not 403)."""
    return HTTPException(status_code=404, detail=detail)


def _conflict(message: str, *, code: str = "active_conflict") -> HTTPException:
    """Return an HTTPException for 409 responses (active run conflict).

    The ``detail`` is a structured dict with ``code`` and ``message`` so the
    client can distinguish ``active_conflict`` (another run in progress) from
    ``bridge_busy`` (channel sentinel lock contention).
    """
    return HTTPException(
        status_code=409,
        detail={"code": code, "message": message},
    )


# ---------------------------------------------------------------------------
# Item helpers
# ---------------------------------------------------------------------------


def _make_item_from_candidate(
    candidate: dict[str, Any], rank: int
) -> dict[str, Any]:
    """Create a pending item dict from a scan candidate."""
    return {
        "job_key": candidate.get("job_key", ""),
        "rank": rank,
        "status": DiscoveryItemStatus.pending.value,
        "title": candidate.get("title"),
        "company": candidate.get("company"),
        "job_id": None,
        "application_id": None,
        "decision": None,
        "score": None,
        "match_artifact_id": None,
        "action_id": None,
        "failure_code": None,
        "skip_reason": None,
        "message": None,
    }


def _items_to_out(items: list[dict[str, Any]]) -> list[DiscoveryItemOut]:
    """Convert raw item dicts to ``DiscoveryItemOut`` schemas."""
    return [
        DiscoveryItemOut(
            job_key=item["job_key"],
            rank=item["rank"],
            status=DiscoveryItemStatus(item["status"]),
            title=item.get("title"),
            company=item.get("company"),
            job_id=item.get("job_id"),
            application_id=item.get("application_id"),
            decision=item.get("decision"),
            score=item.get("score"),
            match_artifact_id=item.get("match_artifact_id"),
            action_id=item.get("action_id"),
            failure_code=item.get("failure_code"),
            skip_reason=item.get("skip_reason"),
            message=item.get("message"),
        )
        for item in items
    ]


def _build_status_out(run: AgentRun) -> DiscoveryStatusOut:
    """Build a ``DiscoveryStatusOut`` from the persisted ``AgentRun``."""
    result = run.result or {}
    items = result.get("items", [])
    non_terminal = {
        DiscoveryItemStatus.pending.value,
        DiscoveryItemStatus.opening.value,
        DiscoveryItemStatus.reading.value,
        DiscoveryItemStatus.persisted.value,
        DiscoveryItemStatus.matching.value,
    }
    processed = sum(1 for item in items if item["status"] not in non_terminal)
    return DiscoveryStatusOut(
        run_id=run.id,
        status=DiscoveryRunStatus(
            result.get("discovery_status", DiscoveryRunStatus.running.value)
        ),
        mode=result.get("mode", "prepare_only"),
        resume_version_id=result.get("resume_version_id", ""),
        total=len(items),
        processed=processed,
        consecutive_failures=result.get("consecutive_failures", 0),
        hard_stop_threshold=result.get(
            "hard_stop_threshold", _HARD_STOP_THRESHOLD
        ),
        items=_items_to_out(items),
        message=result.get("message"),
    )


def get_discovery_run(
    db: Session,
    current_user: UserProfile,
    run_id: str,
) -> AgentRun:
    """Load a discovery run, verifying ownership (404 on failure).

    Cross-user access returns 404 (not 403), matching the codebase convention.
    """
    run = agent_run_repo.get_run(db, run_id)
    if run is None or run.user_id != current_user.id:
        raise _not_found("discovery run not found")
    if run.workflow_type != WORKFLOW_TYPE:
        raise _not_found("discovery run not found")
    return run


def build_status_out(run: AgentRun) -> DiscoveryStatusOut:
    """Public wrapper for ``_build_status_out`` (used by the API layer)."""
    return _build_status_out(run)


# ---------------------------------------------------------------------------
# Active-run interlock
# ---------------------------------------------------------------------------


def _find_active_run(db: Session, user_id: str) -> AgentRun | None:
    """Return the user's running or paused discovery run, or ``None``."""
    return (
        db.execute(
            select(AgentRunModel)
            .where(
                (AgentRunModel.user_id == user_id)
                & (AgentRunModel.workflow_type == WORKFLOW_TYPE)
                & (AgentRunModel.status.in_(["running", "paused"]))
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _find_active_batch_or_discovery_run(
    db: Session, user_id: str
) -> AgentRun | None:
    """Return the user's running/paused batch-loop or discovery run, or None.

    Used at discovery start to enforce cross-workflow mutual exclusion (spec
    §14): only one active bridge-consuming workflow per user at a time.
    """
    return (
        db.execute(
            select(AgentRunModel)
            .where(
                (AgentRunModel.user_id == user_id)
                & (
                    AgentRunModel.workflow_type.in_(
                        [WORKFLOW_TYPE, _BATCH_LOOP_WORKFLOW_TYPE]
                    )
                )
                & (AgentRunModel.status.in_(["running", "paused"]))
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


# ---------------------------------------------------------------------------
# Bridge op wrappers
# ---------------------------------------------------------------------------


async def _scan_visible_jobs(
    channel: UserscriptChannel, limit: int
) -> InstructionResult:
    """Dispatch ``scan_visible_jobs`` and return the result."""
    instruction = make_instruction(
        "scan_visible_jobs",
        max_items=limit,
    )
    return await channel.put_instruction(instruction)


async def _open_job_by_key(
    channel: UserscriptChannel, job_key: str
) -> InstructionResult:
    """Dispatch ``open_job_by_key`` and return the result."""
    instruction = make_instruction(
        "open_job_by_key",
        job_key=job_key,
    )
    return await channel.put_instruction(instruction)


async def _wait_job_detail_ready(
    channel: UserscriptChannel, expected_title: str
) -> InstructionResult:
    """Dispatch ``wait_job_detail_ready`` and return the result."""
    instruction = make_instruction(
        "wait_job_detail_ready",
        expected_title=expected_title,
    )
    return await channel.put_instruction(instruction)


async def _read_jd_pane(
    channel: UserscriptChannel, job_key: str
) -> InstructionResult:
    """Dispatch ``read_jd`` with the pane profile and return the result.

    ``job_key`` is passed so the userscript can bind the read to the correct
    card context (and so the fake channel in tests can route per-key JDs).
    """
    instruction = make_instruction(
        "read_jd",
        selector_profile=_PANE_PROFILE,
        job_key=job_key,
    )
    return await channel.put_instruction(instruction)


# ---------------------------------------------------------------------------
# Run-level helpers
# ---------------------------------------------------------------------------


def _persist_run(
    db: Session,
    run: AgentRun,
    *,
    result: dict[str, Any],
    status: str | None = None,
    finished_at: datetime | None = None,
) -> AgentRun:
    """Persist the run's result (and optionally status) and commit.

    Uses ``flag_modified`` to force SQLAlchemy to detect in-place mutations to
    the JSON ``result`` column. Without this, assigning ``run.result = result``
    when ``result`` is the *same object* as ``run.result`` (obtained via
    ``run.result or {}`` at the top of ``_process_run``) is a no-op from
    SQLAlchemy's perspective — it sees no attribute change and skips the
    UPDATE, so in-place mutations to nested dicts/lists are lost on commit.
    """
    kwargs: dict[str, Any] = {"result": result}
    if status is not None:
        kwargs["status"] = status
    if finished_at is not None:
        kwargs["finished_at"] = finished_at
    agent_run_repo.update_status(db, run, **kwargs)
    flag_modified(run, "result")
    db.commit()
    db.refresh(run)
    return run


def _mark_run_failed(
    db: Session,
    run: AgentRun,
    result: dict[str, Any],
    *,
    failure_code: str,
    message: str,
) -> AgentRun:
    """Mark the run as failed with a failure code and message."""
    result["discovery_status"] = DiscoveryRunStatus.failed.value
    result["failure_code"] = failure_code
    result["message"] = message
    return _persist_run(
        db,
        run,
        result=result,
        status="failed",
        finished_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# already_persisted skip check
# ---------------------------------------------------------------------------


def _has_reusable_communicate_action(
    db: Session, *, application_id: str, user_id: str
) -> bool:
    """Return True if the application already has a reusable communicate action.

    A action is reusable when it is either non-terminal (awaiting
    approval/execution) or has a terminal external result (already submitted).
    This is the ``already_persisted`` skip check (design.md §Identity & Dedup).
    """
    action = application_action_repo.find_reusable_communicate_action(
        db,
        application_id=application_id,
        user_id=user_id,
        include_terminal=True,
    )
    return action is not None


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------


async def start_discovery_run(
    db: Session,
    current_user: UserProfile,
    *,
    resume_version_id: str,
    limit: int,
    mode: str,
    gateway: ModelGateway,
) -> AgentRun:
    """Start (or resume) a discovery run and process items synchronously.

    Creates an ``AgentRun``, scans the visible candidates, serially processes
    each one through open → wait → read → upsert → match → prepare, and
    returns the run. The run's ``result`` JSON carries per-item progress.

    ``mode`` is recorded on the run; the API layer rejects ``auto_execute``
    until the dry-run gate passes. Regardless of mode, processing is
    prepare-only: no auto-approve, no auto-execute.

    Raises ``HTTPException(409)`` if another discovery or batch-loop run is
    already active for this user (``active_conflict``).
    """
    # --- Cross-workflow interlock: reject if any bridge-consuming run is active. ---
    # Resume of an existing paused discovery run happens only via the explicit
    # ``resume_discovery_run`` endpoint. The start path always returns 409
    # ``active_conflict`` when any discovery or batch-loop run is still active,
    # so the caller never silently resumes or duplicates an in-flight run.
    active = _find_active_batch_or_discovery_run(db, current_user.id)
    if active is not None:
        raise _conflict(
            "另一个批量循环或发现任务正在运行，请等待其完成后再试。"
        )

    # --- Verify the userscript bridge is connected. ---
    channel = get_channel()
    if not channel.is_connected():
        raise HTTPException(
            status_code=400,
            detail="油猴脚本未连接，请确保已在 BOSS 推荐列表页安装并运行脚本。",
        )

    # --- Create the discovery run. ---
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=datetime.now(UTC),
        result={
            "discovery_status": DiscoveryRunStatus.running.value,
            "mode": mode,
            "resume_version_id": resume_version_id,
            "limit": limit,
            "consecutive_failures": 0,
            "hard_stop_threshold": _HARD_STOP_THRESHOLD,
            "items": [],
            "message": None,
        },
    )
    db.commit()
    db.refresh(run)

    # --- Take the channel sentinel lock for the entire run. ---
    sentinel = f"discovery:{run.id}"
    try:
        await channel.set_active_application(sentinel)
    except RuntimeError as exc:
        _mark_run_failed(
            db,
            run,
            run.result or {},
            failure_code="bridge_busy",
            message=str(exc),
        )
        raise _conflict(
            "油猴桥接通道被占用，请稍后重试。", code="bridge_busy"
        ) from exc

    return await _process_run(db, current_user, run, channel, gateway=gateway)


async def _resume_run(
    db: Session,
    current_user: UserProfile,
    run: AgentRun,
    *,
    gateway: ModelGateway,
) -> AgentRun:
    """Resume a paused/running discovery run.

    Re-scans the visible jobs to rebuild the userscript's in-memory candidate
    cache, matches returned candidates by stable ``job_key``, then continues
    processing pending items. If re-scan fails, marks non-terminal items
    ``stopped(interrupted)``.
    """
    result = run.result or {}
    result["discovery_status"] = DiscoveryRunStatus.running.value
    result["message"] = None
    agent_run_repo.update_status(db, run, status="running", result=result)
    db.commit()
    db.refresh(run)

    channel = get_channel()
    if not channel.is_connected():
        # Mark non-terminal items as stopped (interrupted).
        _mark_interrupted(result)
        _persist_run(
            db,
            run,
            result=result,
            status="failed",
            finished_at=datetime.now(UTC),
        )
        raise HTTPException(
            status_code=400,
            detail="油猴脚本未连接，无法恢复发现任务。",
        )

    sentinel = f"discovery:{run.id}"
    try:
        await channel.set_active_application(sentinel)
    except RuntimeError as exc:
        raise _conflict(
            "油猴桥接通道被占用，请稍后重试。", code="bridge_busy"
        ) from exc

    return await _process_run(
        db, current_user, run, channel, gateway=gateway, is_resume=True
    )


def _mark_interrupted(result: dict[str, Any]) -> None:
    """Mark non-terminal items as ``stopped`` with an interrupted message."""
    non_terminal = {
        DiscoveryItemStatus.pending.value,
        DiscoveryItemStatus.opening.value,
        DiscoveryItemStatus.reading.value,
        DiscoveryItemStatus.persisted.value,
        DiscoveryItemStatus.matching.value,
    }
    for item in result.get("items", []):
        if item["status"] in non_terminal:
            item["status"] = DiscoveryItemStatus.stopped.value
            item["message"] = "interrupted: rescan required"


async def _process_run(
    db: Session,
    current_user: UserProfile,
    run: AgentRun,
    channel: UserscriptChannel,
    *,
    gateway: ModelGateway,
    is_resume: bool = False,
) -> AgentRun:
    """Process the discovery run: scan → per-item open/wait/read/upsert/match/prepare.

    On resume, re-scans to rebuild the userscript cache before processing.
    """
    result = run.result or {}
    resume_version_id: str = result.get("resume_version_id", "")
    consecutive_failures: int = result.get("consecutive_failures", 0)
    hard_stop_threshold: int = result.get(
        "hard_stop_threshold", _HARD_STOP_THRESHOLD
    )

    # --- Scan visible jobs (or re-scan on resume). ---
    scan_result = await _scan_visible_jobs(channel, limit=result.get("limit", 10))
    if not scan_result.success:
        failure_code = scan_result.error or "scan_failed"
        result["failure_code"] = failure_code
        result["message"] = f"扫描推荐列表失败：{failure_code}"
        # On resume, mark non-terminal items as interrupted rather than failing.
        if is_resume:
            _mark_interrupted(result)
            result["discovery_status"] = DiscoveryRunStatus.failed.value
            _persist_run(
                db, run, result=result, status="failed",
                finished_at=datetime.now(UTC),
            )
        else:
            _mark_run_failed(
                db, run, result, failure_code=failure_code,
                message=f"扫描推荐列表失败：{failure_code}",
            )
        _cleanup_channel(channel)
        return run

    candidates = scan_result.job_candidates or []
    if not candidates:
        result["discovery_status"] = DiscoveryRunStatus.completed.value
        result["message"] = "推荐列表为空（no_visible_jobs），没有可采集的职位。"
        _persist_run(
            db, run, result=result, status="succeeded",
            finished_at=datetime.now(UTC),
        )
        _cleanup_channel(channel)
        return run

    # --- Build or merge items from candidates. ---
    existing_items: list[dict[str, Any]] = result.get("items", [])
    if is_resume and existing_items:
        # Merge: keep processed items, update pending items with re-scanned
        # candidate metadata. The userscript cache is rebuilt by the scan.
        items = existing_items
        candidate_map = {c["job_key"]: c for c in candidates if c.get("job_key")}
        for item in items:
            if item["status"] == DiscoveryItemStatus.pending.value:
                c = candidate_map.get(item["job_key"])
                if c is None:
                    # Key no longer visible — mark stopped (interrupted).
                    item["status"] = DiscoveryItemStatus.stopped.value
                    item["message"] = "interrupted: job_key not found in rescan"
    else:
        items = [
            _make_item_from_candidate(c, rank + 1)
            for rank, c in enumerate(candidates)
        ]
        result["items"] = items
        result["total"] = len(items)

    # --- Process each pending item. ---
    for idx, item in enumerate(items):
        # Skip already-processed items.
        if item["status"] != DiscoveryItemStatus.pending.value:
            continue

        # Check for hard-stop from prior iterations.
        if consecutive_failures >= hard_stop_threshold:
            item["status"] = DiscoveryItemStatus.stopped.value
            item["message"] = "hard_stopped: consecutive failure threshold reached"
            continue

        # --- Process one item. ---
        try:
            decision, score, artifact_id, action_id, application_id, job_id = (
                await _process_single_item(
                    db,
                    current_user,
                    channel,
                    item=item,
                    resume_version_id=resume_version_id,
                    agent_run_id=run.id,
                    gateway=gateway,
                )
            )
        except _HardStopError as exc:
            # Hard-stop conditions (unexpected_navigation, page_mismatch, etc.)
            item["status"] = DiscoveryItemStatus.failed.value
            item["failure_code"] = exc.code
            item["message"] = exc.message
            consecutive_failures += 1
            # Mark remaining items as stopped and hard-stop the run.
            for remaining in items[idx + 1 :]:
                if remaining["status"] == DiscoveryItemStatus.pending.value:
                    remaining["status"] = DiscoveryItemStatus.stopped.value
                    remaining["message"] = (
                        "hard_stopped: consecutive failure threshold reached"
                    )
            result["items"] = items
            result["consecutive_failures"] = consecutive_failures
            result["discovery_status"] = DiscoveryRunStatus.hard_stopped.value
            result["message"] = f"发现任务已硬停止：{exc.message}"
            _persist_run(
                db, run, result=result, status="failed",
                finished_at=datetime.now(UTC),
            )
            _cleanup_channel(channel)
            return run
        except HTTPException as exc:
            item["status"] = DiscoveryItemStatus.failed.value
            item["failure_code"] = "prepare_failed"
            item["message"] = (
                exc.detail if isinstance(exc.detail, str)
                else json.dumps(exc.detail, ensure_ascii=False)
            )
            consecutive_failures += 1
        except Exception as exc:
            _log.warning(
                "boss_discovery.item_failed",
                run_id=run.id,
                job_key=item["job_key"],
                error_type=type(exc).__name__,
                error=str(exc),
            )
            item["status"] = DiscoveryItemStatus.failed.value
            item["failure_code"] = "item_error"
            item["message"] = f"{type(exc).__name__}: {exc}"
            consecutive_failures += 1
        else:
            # --- Success path. ---
            item["job_id"] = job_id
            item["application_id"] = application_id
            item["decision"] = decision
            item["score"] = score
            item["match_artifact_id"] = artifact_id
            item["action_id"] = action_id

            if decision == MatchDecision.communicate.value:
                item["status"] = DiscoveryItemStatus.prepared.value
            elif decision == MatchDecision.skip.value:
                item["status"] = DiscoveryItemStatus.skipped.value
            elif decision == MatchDecision.needs_review.value:
                item["status"] = DiscoveryItemStatus.needs_review.value

            # skip / needs_review / prepared are NOT incidents — reset counter.
            consecutive_failures = 0

        # --- Persist after each item. ---
        result["items"] = items
        result["consecutive_failures"] = consecutive_failures
        _persist_run(db, run, result=result, status="running")

        # --- Check hard-stop after processing. ---
        if consecutive_failures >= hard_stop_threshold:
            _log.warning(
                "boss_discovery.hard_stopped",
                run_id=run.id,
                consecutive_failures=consecutive_failures,
            )
            for remaining in items[idx + 1 :]:
                if remaining["status"] == DiscoveryItemStatus.pending.value:
                    remaining["status"] = DiscoveryItemStatus.stopped.value
                    remaining["message"] = (
                        "hard_stopped: consecutive failure threshold reached"
                    )
            result["items"] = items
            result["discovery_status"] = DiscoveryRunStatus.hard_stopped.value
            result["message"] = (
                f"连续 {consecutive_failures} 次失败，发现任务已硬停止"
            )
            _persist_run(
                db, run, result=result, status="failed",
                finished_at=datetime.now(UTC),
            )
            _cleanup_channel(channel)
            return run

    # --- All items processed — mark completed. ---
    result["items"] = items
    result["discovery_status"] = DiscoveryRunStatus.completed.value
    result["message"] = "发现任务已完成"
    _persist_run(
        db, run, result=result, status="succeeded",
        finished_at=datetime.now(UTC),
    )
    _cleanup_channel(channel)
    return run


def _cleanup_channel(channel: UserscriptChannel) -> None:
    """Clear the channel sentinel lock once at run completion."""
    channel.clear()


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------


class _HardStopError(Exception):
    """Internal: signals a hard-stop condition (unexpected_navigation, etc.)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# Failure codes that are hard-stop conditions, not item-level failures.
_HARD_STOP_CODES = frozenset({
    "unexpected_navigation",
    "page_mismatch",
    "captcha_required",
    "rate_limited",
})


async def _process_single_item(
    db: Session,
    current_user: UserProfile,
    channel: UserscriptChannel,
    *,
    item: dict[str, Any],
    resume_version_id: str,
    agent_run_id: str,
    gateway: ModelGateway,
) -> tuple[str, float, str | None, str | None, str | None, str]:
    """Process one candidate: open → wait → read → upsert → skip-check → match → prepare.

    Returns ``(decision, score, artifact_id, action_id, application_id, job_id)``.
    ``action_id`` / ``application_id`` are ``None`` when the decision is not
    ``communicate`` or the item was skipped.

    Raises ``_HardStopError`` on hard-stop conditions (unexpected navigation,
    page mismatch, captcha, rate limit).
    Raises ``HTTPException`` on prepare validation failures.
    """
    job_key = item["job_key"]
    expected_title = item.get("title") or ""

    # --- 1. Open the job by clicking the card root. ---
    item["status"] = DiscoveryItemStatus.opening.value
    open_result = await _open_job_by_key(channel, job_key)
    if not open_result.success:
        _check_hard_stop(open_result.error)
        # Item-level failure.
        raise Exception(f"open_job_by_key failed: {open_result.error}")

    # --- 2. Wait for the pane to show the expected title. ---
    item["status"] = DiscoveryItemStatus.reading.value
    wait_result = await _wait_job_detail_ready(channel, expected_title)
    if not wait_result.success:
        _check_hard_stop(wait_result.error)
        raise Exception(f"wait_job_detail_ready failed: {wait_result.error}")

    # --- 3. Read the JD from the pane. ---
    read_result = await _read_jd_pane(channel, job_key)
    if not read_result.success or read_result.jd is None:
        _check_hard_stop(read_result.error)
        raise Exception(f"read_jd failed: {read_result.error}")

    jd_dict = read_result.jd

    # Merge company from the scan candidate (pane has no company field).
    if not jd_dict.get("company") and item.get("company"):
        jd_dict["company"] = item["company"]

    # --- 4. Check sparseness. ---
    if is_jd_too_sparse(jd_dict):
        raise Exception("jd_too_sparse: missing title or description")

    # --- 5. Upsert the job with external_id_override=job_key. ---
    try:
        job, _is_new_job = upsert_job_from_browser_jd(
            db,
            user_id=current_user.id,
            jd_dict=jd_dict,
            agent_run_id=agent_run_id,
            external_id_override=job_key,
        )
    except ValueError as exc:
        raise Exception(f"upsert_failed: {exc}") from exc

    # --- 6. Create or reuse an application record. ---
    from app.schemas.application import ApplicationStatus

    application = application_repo.find_duplicate(
        db,
        user_id=current_user.id,
        job_id=job.id,
        resume_version_id=resume_version_id,
    )
    if application is None:
        event = application_repo.build_event(
            type="created",
            actor="agent",
            to_status=ApplicationStatus.planned.value,
            summary="BOSS 推荐职位发现投递记录已创建",
            metadata={
                "job_id": job.id,
                "resume_version_id": resume_version_id,
                "agent_run_id": agent_run_id,
                "source": "boss_recommended_discovery",
            },
        )
        application = application_repo.create_for_user(
            db,
            user_id=current_user.id,
            job_id=job.id,
            resume_version_id=resume_version_id,
            status=ApplicationStatus.planned.value,
            timeline=[event],
        )

    item["status"] = DiscoveryItemStatus.persisted.value
    item["job_id"] = job.id
    item["application_id"] = application.id
    db.flush()

    # --- 7. already_persisted skip check. ---
    if _has_reusable_communicate_action(
        db, application_id=application.id, user_id=current_user.id
    ):
        # Skip — this job+resume already has durable communicate state.
        # Not a failure, not an incident. Represented as skip_reason, not
        # failure_code.
        item["skip_reason"] = "already_persisted"
        return (
            MatchDecision.skip.value,
            0.0,
            None,
            None,
            application.id,
            job.id,
        )

    # --- 8. Match. ---
    item["status"] = DiscoveryItemStatus.matching.value
    _match_run, artifact, execution, _safety_downgraded, _raw_msg = (
        await run_boss_match_decision(
            db,
            current_user,
            job_id=job.id,
            resume_version_id=resume_version_id,
            gateway=gateway,
        )
    )

    output = execution.output
    decision = output.decision
    score = output.score
    artifact_id = artifact.id

    # --- 9. Prepare (only when communicate). ---
    action_id: str | None = None

    if decision == MatchDecision.communicate:
        action = prepare_communicate_action(
            db,
            current_user,
            job.id,
            resume_version_id=resume_version_id,
            match_artifact_id=artifact_id,
        )
        action_id = action.id

    return (
        decision.value,
        score,
        artifact_id,
        action_id,
        application.id,
        job.id,
    )


def _check_hard_stop(error: str | None) -> None:
    """Raise ``_HardStopError`` if the error code is a hard-stop condition."""
    if error and error in _HARD_STOP_CODES:
        raise _HardStopError(
            code=error,
            message=f"hard_stop: {error}",
        )


# ---------------------------------------------------------------------------
# Pause / Resume (crash-recovery only in v0.1)
# ---------------------------------------------------------------------------


def pause_discovery_run(
    db: Session,
    current_user: UserProfile,
    run_id: str,
) -> AgentRun:
    """Pause a running discovery run (crash-recovery only in v0.1).

    Since processing runs synchronously inside the start/resume request, pause
    cannot interrupt an in-flight request; it applies to runs that are not
    actively being processed (e.g. a stuck ``running`` run left by a crashed
    worker), marking them ``paused`` so they can be resumed later. If the run
    is already terminal, this is a no-op.
    """
    run = get_discovery_run(db, current_user, run_id)
    result = run.result or {}
    discovery_status = DiscoveryRunStatus(
        result.get("discovery_status", "running")
    )

    if discovery_status in (
        DiscoveryRunStatus.completed,
        DiscoveryRunStatus.hard_stopped,
        DiscoveryRunStatus.failed,
    ):
        return run

    result["discovery_status"] = DiscoveryRunStatus.paused.value
    result["message"] = "用户已暂停发现任务"
    agent_run_repo.update_status(db, run, status="paused", result=result)
    db.commit()
    db.refresh(run)
    return run


async def resume_discovery_run(
    db: Session,
    current_user: UserProfile,
    run_id: str,
    *,
    gateway: ModelGateway,
) -> AgentRun:
    """Resume a paused discovery run."""
    run = get_discovery_run(db, current_user, run_id)
    result = run.result or {}
    discovery_status = DiscoveryRunStatus(
        result.get("discovery_status", "running")
    )

    if discovery_status != DiscoveryRunStatus.paused:
        return run

    return await _resume_run(db, current_user, run, gateway=gateway)


__all__ = [
    "WORKFLOW_TYPE",
    "build_status_out",
    "get_discovery_run",
    "pause_discovery_run",
    "resume_discovery_run",
    "start_discovery_run",
]
