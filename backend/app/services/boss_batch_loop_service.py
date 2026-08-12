"""BOSS recommended-job batch-loop service.

This service implements the serial batch traversal of the BOSS recommended-job
list (``design.md`` §Architecture):

1. Create an ``AgentRun`` (``workflow_type=boss_recommended_job_batch_loop``)
   to hold the batch progress in ``AgentRun.result``.
2. For each ``job_id`` (up to ``limit``), serially run:
   - **inspect** — verify the job exists and is owned by the current user.
   - **match** — run ``run_boss_match_decision`` to get a decision.
   - **prepare** — when the decision is ``communicate``, run
     ``prepare_communicate_action`` to draft an approval-required action.
3. Stop at ``approval_required`` or ``needs_review`` — never auto-approve or
   auto-execute (Phase 1 ``prepare_only``).
4. Hard-stop after 3 consecutive ``failed`` items (R3).

Phase 2 (``auto_execute``) is rejected at the API layer until the dry-run gate
passes. Even when the gate passes and ``auto_execute`` is accepted, this
service still runs prepare-only semantics: it never auto-approves or
auto-executes (Phase 2 execution automation is not implemented yet).

Safety invariants:

- The batch loop never auto-approves or auto-executes. Every external side
  effect still goes through the existing approval + idempotency guard
  (``evolution-contracts.md`` §1).
- Cross-user access returns 404 (not 403).
- Serial execution only — no concurrent userscript-bridge instructions.
- ``skip`` / ``needs_review`` are item outcomes, not incidents.
- Per-item progress is stored in ``AgentRun.result["items"]`` as a list of
  dicts (``design.md`` §Persistence).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import JobPosting, UserProfile
from app.db.repositories import agent_run_repo
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway
from app.schemas.boss_batch_loop import (
    BatchItemStatus,
    BatchLoopItemOut,
    BatchLoopStatusOut,
    BatchRunStatus,
)
from app.schemas.boss_match_decision import MatchDecision
from app.services.boss_communicate_service import prepare_communicate_action
from app.services.boss_match_service import run_boss_match_decision

_log = get_logger("app.services.boss_batch_loop_service")

#: The workflow type stored on the batch-loop ``AgentRun``.
WORKFLOW_TYPE = "boss_recommended_job_batch_loop"

#: Consecutive-failure threshold (R3). 3 consecutive ``failed`` items stop the
#: batch and mark remaining items as ``stopped``.
_HARD_STOP_THRESHOLD = 3


def _not_found(detail: str) -> HTTPException:
    """Return an HTTPException for 404 responses (not 403)."""
    return HTTPException(status_code=404, detail=detail)


def _make_item(job_id: str) -> dict[str, Any]:
    """Create a pending item dict for ``AgentRun.result["items"]``."""
    return {
        "job_id": job_id,
        "status": BatchItemStatus.pending.value,
        "decision": None,
        "score": None,
        "match_artifact_id": None,
        "action_id": None,
        "application_id": None,
        "error": None,
    }


def _items_to_out(items: list[dict[str, Any]]) -> list[BatchLoopItemOut]:
    """Convert raw item dicts to ``BatchLoopItemOut`` schemas."""
    return [
        BatchLoopItemOut(
            job_id=item["job_id"],
            status=BatchItemStatus(item["status"]),
            decision=item.get("decision"),
            score=item.get("score"),
            match_artifact_id=item.get("match_artifact_id"),
            action_id=item.get("action_id"),
            application_id=item.get("application_id"),
            error=item.get("error"),
        )
        for item in items
    ]


def _build_status_out(run: AgentRun) -> BatchLoopStatusOut:
    """Build a ``BatchLoopStatusOut`` from the persisted ``AgentRun``."""
    result = run.result or {}
    items = result.get("items", [])
    processed = sum(
        1
        for item in items
        if item["status"]
        not in (BatchItemStatus.pending.value, BatchItemStatus.inspecting.value)
    )
    return BatchLoopStatusOut(
        run_id=run.id,
        status=BatchRunStatus(result.get("batch_status", BatchRunStatus.running.value)),
        mode=result.get("mode", "prepare_only"),
        resume_version_id=result.get("resume_version_id", ""),
        total=len(items),
        processed=processed,
        consecutive_failures=result.get("consecutive_failures", 0),
        hard_stop_threshold=result.get("hard_stop_threshold", _HARD_STOP_THRESHOLD),
        items=_items_to_out(items),
        message=result.get("message"),
    )


def get_batch_run(
    db: Session,
    current_user: UserProfile,
    run_id: str,
) -> AgentRun:
    """Load a batch-loop run, verifying ownership (404 on failure).

    Cross-user access returns 404 (not 403), matching the codebase convention.
    """
    run = agent_run_repo.get_run(db, run_id)
    if run is None or run.user_id != current_user.id:
        raise _not_found("batch run not found")
    if run.workflow_type != WORKFLOW_TYPE:
        raise _not_found("batch run not found")
    return run


def build_status_out(run: AgentRun) -> BatchLoopStatusOut:
    """Public wrapper for ``_build_status_out`` (used by the API layer)."""
    return _build_status_out(run)


async def start_batch_loop(
    db: Session,
    current_user: UserProfile,
    *,
    resume_version_id: str,
    job_ids: list[str],
    limit: int,
    mode: str,
    gateway: ModelGateway,
) -> AgentRun:
    """Start (or resume) a batch loop and process items synchronously.

    Creates an ``AgentRun``, serially processes up to ``limit`` jobs, and
    returns the run. The run's ``result`` JSON carries per-item progress.

    ``mode`` is recorded on the run; the API layer rejects
    ``"auto_execute"`` until the dry-run gate passes. Regardless of mode,
    processing is prepare-only: no auto-approve, no auto-execute.

    If a run is already in progress for the same user (``running`` or
    ``paused``), it is resumed instead of creating a new one. This keeps the
    "single active batch" invariant simple: one batch run per user at a time.
    """
    # --- Look for an existing running/paused batch for this user. ---
    existing = _find_active_run(db, current_user.id)
    if existing is not None:
        return await _resume_run(db, current_user, existing, gateway=gateway)

    # --- Create the batch run. ---
    items = [_make_item(jid) for jid in job_ids]
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=datetime.now(UTC),
        result={
            "batch_status": BatchRunStatus.running.value,
            "mode": mode,
            "resume_version_id": resume_version_id,
            "consecutive_failures": 0,
            "hard_stop_threshold": _HARD_STOP_THRESHOLD,
            "items": items,
            "message": None,
        },
    )
    db.commit()
    db.refresh(run)

    return await _process_items(db, current_user, run, gateway=gateway)


def _find_active_run(db: Session, user_id: str) -> AgentRun | None:
    """Return the user's running or paused batch run, or ``None``."""
    from sqlalchemy import select

    from app.db.models.models import AgentRun as _AR

    return (
        db.execute(
            select(_AR)
            .where(
                (_AR.user_id == user_id)
                & (_AR.workflow_type == WORKFLOW_TYPE)
                & (
                    _AR.status.in_(["running", "paused"])
                )
            )
            .order_by(_AR.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


async def _resume_run(
    db: Session,
    current_user: UserProfile,
    run: AgentRun,
    *,
    gateway: ModelGateway,
) -> AgentRun:
    """Resume a paused/running batch run by processing remaining items."""
    result = run.result or {}
    result["batch_status"] = BatchRunStatus.running.value
    result["message"] = None
    agent_run_repo.update_status(db, run, status="running", result=result)
    db.commit()
    db.refresh(run)
    return await _process_items(db, current_user, run, gateway=gateway)


def pause_batch_run(
    db: Session,
    current_user: UserProfile,
    run_id: str,
) -> AgentRun:
    """Pause a running batch run.

    Since batch processing runs synchronously inside the start/resume
    request, pause cannot interrupt an in-flight request; it applies to runs
    that are not actively being processed (e.g. a stuck ``running`` run left
    by a crashed worker), marking them ``paused`` so they can be resumed
    later. If the run is already terminal, this is a no-op.
    """
    run = get_batch_run(db, current_user, run_id)
    result = run.result or {}
    batch_status = BatchRunStatus(result.get("batch_status", "running"))

    if batch_status in (
        BatchRunStatus.completed,
        BatchRunStatus.hard_stopped,
        BatchRunStatus.failed,
    ):
        return run

    result["batch_status"] = BatchRunStatus.paused.value
    result["message"] = "用户已暂停批量循环"
    agent_run_repo.update_status(
        db, run, status="paused", result=result
    )
    db.commit()
    db.refresh(run)
    return run


async def resume_batch_run(
    db: Session,
    current_user: UserProfile,
    run_id: str,
    *,
    gateway: ModelGateway,
) -> AgentRun:
    """Resume a paused batch run."""
    run = get_batch_run(db, current_user, run_id)
    result = run.result or {}
    batch_status = BatchRunStatus(result.get("batch_status", "running"))

    if batch_status != BatchRunStatus.paused:
        return run

    return await _resume_run(db, current_user, run, gateway=gateway)


async def _process_items(
    db: Session,
    current_user: UserProfile,
    run: AgentRun,
    *,
    gateway: ModelGateway,
) -> AgentRun:
    """Serially process pending items in the batch run.

    For each item:
    1. Mark ``inspecting``.
    2. Verify job ownership (404 → ``failed`` item).
    3. Run match decision.
    4. If ``communicate`` → prepare action → ``prepared``.
       If ``skip`` → ``skipped``.
       If ``needs_review`` → ``needs_review``.
    5. On exception → ``failed``, increment consecutive_failures.
    6. If consecutive_failures >= threshold → hard-stop remaining items.
    7. Update ``AgentRun.result`` after each item.
    """
    result = run.result or {}
    items: list[dict[str, Any]] = result.get("items", [])
    resume_version_id: str = result.get("resume_version_id", "")
    consecutive_failures: int = result.get("consecutive_failures", 0)
    hard_stop_threshold: int = result.get(
        "hard_stop_threshold", _HARD_STOP_THRESHOLD
    )

    for idx, item in enumerate(items):
        # --- Skip already-processed items (resume case). ---
        if item["status"] != BatchItemStatus.pending.value:
            continue

        # --- Check for hard-stop from prior iterations. ---
        if consecutive_failures >= hard_stop_threshold:
            item["status"] = BatchItemStatus.stopped.value
            item["error"] = "hard_stopped: consecutive failure threshold reached"
            continue

        # --- Mark as inspecting. ---
        item["status"] = BatchItemStatus.inspecting.value
        result["items"] = items
        result["consecutive_failures"] = consecutive_failures
        agent_run_repo.update_status(db, run, status="running", result=result)
        db.commit()
        db.refresh(run)

        # --- Process the item. ---
        try:
            decision, score, artifact_id, action_id, application_id = (
                await _process_single_item(
                    db,
                    current_user,
                    job_id=item["job_id"],
                    resume_version_id=resume_version_id,
                    gateway=gateway,
                )
            )
        except HTTPException as exc:
            # Ownership failures (404) or validation failures (422) → failed
            # item, not a crash.
            item["status"] = BatchItemStatus.failed.value
            item["error"] = exc.detail if isinstance(exc.detail, str) else json.dumps(
                exc.detail, ensure_ascii=False
            )
            consecutive_failures += 1
        except Exception as exc:
            _log.warning(
                "boss_batch_loop.item_failed",
                run_id=run.id,
                job_id=item["job_id"],
                error_type=type(exc).__name__,
                error=str(exc),
            )
            item["status"] = BatchItemStatus.failed.value
            item["error"] = f"{type(exc).__name__}: {exc}"
            consecutive_failures += 1
        else:
            # --- Success path. ---
            item["decision"] = decision
            item["score"] = score
            item["match_artifact_id"] = artifact_id
            item["action_id"] = action_id
            item["application_id"] = application_id

            if decision == MatchDecision.communicate.value:
                item["status"] = BatchItemStatus.prepared.value
            elif decision == MatchDecision.skip.value:
                item["status"] = BatchItemStatus.skipped.value
            elif decision == MatchDecision.needs_review.value:
                item["status"] = BatchItemStatus.needs_review.value

            # skip and needs_review are NOT incidents — reset the counter.
            consecutive_failures = 0

        # --- Persist after each item. ---
        result["items"] = items
        result["consecutive_failures"] = consecutive_failures
        agent_run_repo.update_status(db, run, status="running", result=result)
        db.commit()
        db.refresh(run)

        # --- Check hard-stop after processing. ---
        if consecutive_failures >= hard_stop_threshold:
            _log.warning(
                "boss_batch_loop.hard_stopped",
                run_id=run.id,
                consecutive_failures=consecutive_failures,
            )
            # Mark remaining pending items as stopped.
            for remaining in items[idx + 1 :]:
                if remaining["status"] == BatchItemStatus.pending.value:
                    remaining["status"] = BatchItemStatus.stopped.value
                    remaining["error"] = (
                        "hard_stopped: consecutive failure threshold reached"
                    )
            result["items"] = items
            result["batch_status"] = BatchRunStatus.hard_stopped.value
            result["message"] = (
                f"连续 {consecutive_failures} 次失败，批量循环已硬停止"
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                result=result,
            )
            db.commit()
            db.refresh(run)
            return run

    # --- All items processed — mark completed. ---
    result["items"] = items
    result["batch_status"] = BatchRunStatus.completed.value
    result["message"] = "批量循环已完成"
    agent_run_repo.update_status(
        db,
        run,
        status="succeeded",
        finished_at=datetime.now(UTC),
        result=result,
    )
    db.commit()
    db.refresh(run)
    return run


async def _process_single_item(
    db: Session,
    current_user: UserProfile,
    *,
    job_id: str,
    resume_version_id: str,
    gateway: ModelGateway,
) -> tuple[str, float, str | None, str | None, str | None]:
    """Process one job: inspect (verify) → match → prepare.

    Returns ``(decision, score, artifact_id, action_id, application_id)``.
    ``action_id`` / ``application_id`` are ``None`` when the decision is not
    ``communicate``.

    Raises ``HTTPException`` on ownership/validation failures (caller catches
    and records as ``failed`` item).
    """
    # --- Inspect: verify job exists and is owned by the current user. ---
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        raise _not_found("job not found")

    # --- Match: run the match-decision model. ---
    run, artifact, execution, _safety_downgraded, _raw_msg = (
        await run_boss_match_decision(
            db,
            current_user,
            job_id=job_id,
            resume_version_id=resume_version_id,
            gateway=gateway,
        )
    )

    output = execution.output
    decision = output.decision
    score = output.score
    artifact_id = artifact.id

    # --- Prepare: only when the decision is communicate. ---
    action_id: str | None = None
    application_id: str | None = None

    if decision == MatchDecision.communicate:
        action = prepare_communicate_action(
            db,
            current_user,
            job_id,
            resume_version_id=resume_version_id,
            match_artifact_id=artifact_id,
        )
        action_id = action.id
        application_id = action.application_id

    return decision.value, score, artifact_id, action_id, application_id


__all__ = [
    "WORKFLOW_TYPE",
    "build_status_out",
    "get_batch_run",
    "pause_batch_run",
    "resume_batch_run",
    "start_batch_loop",
]
