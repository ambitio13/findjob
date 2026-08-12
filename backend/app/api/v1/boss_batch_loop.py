"""BOSS recommended-job batch-loop router.

Exposes four endpoints under ``/boss/recommended-jobs/batch-loop``:

1. ``POST /batch-loop`` — start (or resume) a batch run. Accepts a list of
   ``job_ids`` and serially processes each (inspect → match → prepare),
   stopping at ``approval_required`` or ``needs_review``. Returns the full
   batch status.
2. ``GET /batch-loop/{run_id}`` — poll the batch run status.
3. ``POST /batch-loop/{run_id}/pause`` — pause a running batch. Since
   processing is synchronous, pause takes effect between items.
4. ``POST /batch-loop/{run_id}/resume`` — resume a paused batch.

Safety invariants (``evolution-contracts.md`` §1, §11):

- All endpoints are authenticated (``X-User-Id``).
- Cross-user access returns 404 (not 403).
- ``mode=auto_execute`` is rejected with HTTP 422 until the dry-run gate
  passes (``boss_dry_run_gate.assert_auto_execute_allowed``).
- Static route segments (``/batch-loop``, ``/batch-loop/{run_id}/pause``,
  ``/batch-loop/{run_id}/resume``) are registered before the parameterized
  ``/{job_id}/...`` segments of the sibling routers — the ``batch-loop``
  prefix avoids collision with ``/{job_id}`` because it is not a valid UUID.
- The batch loop never auto-approves or auto-executes in ``prepare_only``
  mode.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session, get_model_gateway_dep
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.models_gateway.base import ModelGateway
from app.schemas.boss_batch_loop import (
    BatchLoopPauseOut,
    BatchLoopRequest,
    BatchLoopStatusOut,
)
from app.services import boss_dry_run_gate
from app.services.boss_batch_loop_service import (
    build_status_out,
    get_batch_run,
    pause_batch_run,
    resume_batch_run,
    start_batch_loop,
)

_log = get_logger("app.api.v1.boss_batch_loop")

router = APIRouter(prefix="/boss/recommended-jobs", tags=["boss-batch-loop"])


@router.post("/batch-loop", response_model=BatchLoopStatusOut)
async def start_batch_loop_endpoint(
    payload: BatchLoopRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> BatchLoopStatusOut:
    """Start (or resume) a batch loop over the user's recommended jobs.

    Serially processes up to ``limit`` jobs: inspect → match → prepare per
    job, stopping at ``approval_required`` or ``needs_review``. In
    ``prepare_only`` mode (the default) the loop never auto-approves or
    auto-executes — every prepared action requires explicit human approval.

    ``mode=auto_execute`` is rejected with HTTP 422 until the dry-run gate
    passes (10 consecutive incident-free runs + 2 duplicate detections).
    """
    # --- Enforce the dry-run gate for auto_execute mode. ---
    if payload.mode == "auto_execute":
        boss_dry_run_gate.assert_auto_execute_allowed()

    # --- Apply the limit to the job_ids list. ---
    job_ids = payload.job_ids[: payload.limit]

    run = await start_batch_loop(
        db,
        current_user,
        resume_version_id=payload.resume_version_id,
        job_ids=job_ids,
        limit=payload.limit,
        mode=payload.mode,
        gateway=gateway,
    )
    return build_status_out(run)


@router.get("/batch-loop/{run_id}", response_model=BatchLoopStatusOut)
async def get_batch_loop_status_endpoint(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> BatchLoopStatusOut:
    """Poll the status of a batch run.

    Returns the full per-item progress. Cross-user access returns 404.
    """
    run = get_batch_run(db, current_user, run_id)
    return build_status_out(run)


@router.post("/batch-loop/{run_id}/pause", response_model=BatchLoopPauseOut)
async def pause_batch_loop_endpoint(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> BatchLoopPauseOut:
    """Pause a running batch loop.

    Since batch processing runs synchronously inside the start/resume
    request, pause cannot interrupt an in-flight request; it applies to runs
    that are not actively being processed (e.g. a stuck ``running`` run),
    marking them ``paused`` for later resume. Already-terminal runs are a
    no-op.
    """
    run = pause_batch_run(db, current_user, run_id)
    result = run.result or {}
    message = result.get("message", "批量循环已暂停")
    return BatchLoopPauseOut(
        run_id=run.id,
        status=result.get("batch_status", "paused"),
        message=message,
    )


@router.post("/batch-loop/{run_id}/resume", response_model=BatchLoopStatusOut)
async def resume_batch_loop_endpoint(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> BatchLoopStatusOut:
    """Resume a paused batch loop.

    Continues processing remaining ``pending`` items. Non-paused runs are a
    no-op (returns the current status).
    """
    run = await resume_batch_run(db, current_user, run_id, gateway=gateway)
    return build_status_out(run)


__all__ = ["router"]
