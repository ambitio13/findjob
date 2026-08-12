"""BOSS recommended-job discovery router.

Exposes four endpoints under ``/boss/recommended-jobs/discovery``:

1. ``POST /discovery`` — start a discovery run. Scans the visible
   recommended-job cards, then serially processes each through open → wait →
   read → upsert → match → prepare, stopping at ``approval_required`` or
   ``needs_review``. Returns the full run status.
2. ``GET /discovery/{run_id}`` — poll the discovery run status.
3. ``POST /discovery/{run_id}/pause`` — pause a running discovery run
   (crash-recovery only in v0.1).
4. ``POST /discovery/{run_id}/resume`` — resume a paused discovery run.

Safety invariants (``evolution-contracts.md`` §1, §11, §14):

- All endpoints are authenticated (``X-User-Id``).
- Cross-user access returns 404 (not 403).
- ``mode=auto_execute`` is always rejected with HTTP 422 in v0.1 — the
  discovery pipeline is strictly ``prepare_only`` in Phase 1, regardless of
  the dry-run gate status.
- Static route segments (``/discovery``, ``/discovery/{run_id}/pause``,
  ``/discovery/{run_id}/resume``) are registered before the parameterized
  ``/{job_id}/...`` segments of the sibling routers — the ``discovery``
  prefix avoids collision with ``/{job_id}`` because it is not a valid UUID.
- The discovery pipeline never auto-approves or auto-executes in
  ``prepare_only`` mode.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session, get_model_gateway_dep
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.models_gateway.base import ModelGateway
from app.schemas.boss_recommended_discovery import (
    DiscoveryPauseOut,
    DiscoveryRequest,
    DiscoveryStatusOut,
)
from app.services.boss_recommended_discovery_service import (
    build_status_out,
    get_discovery_run,
    pause_discovery_run,
    resume_discovery_run,
    start_discovery_run,
)

_log = get_logger("app.api.v1.boss_recommended_discovery")

router = APIRouter(prefix="/boss/recommended-jobs", tags=["boss-discovery"])


@router.post("/discovery", response_model=DiscoveryStatusOut)
async def start_discovery_endpoint(
    payload: DiscoveryRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> DiscoveryStatusOut:
    """Start (or resume) a discovery run over the user's recommended-job list.

    Scans the visible cards on the BOSS recommended list page (zero
    navigation), then serially processes each candidate: open card root → wait
    for pane → read JD → upsert → match → prepare. In ``prepare_only`` mode
    (the default) the pipeline never auto-approves or auto-executes — every
    prepared action requires explicit human approval.

    ``mode=auto_execute`` is always rejected with HTTP 422 in v0.1 — the
    discovery pipeline is strictly ``prepare_only`` in Phase 1, regardless of
    the dry-run gate status.

    ``limit`` default 3, hard cap 10 (sync execution budget).
    """
    # --- Reject auto_execute: v0.1 is strictly prepare_only. ---
    if payload.mode == "auto_execute":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "auto_execute_not_supported",
                "message": (
                    "discovery 流水线在 v0.1 仅支持 prepare_only 模式，"
                    "auto_execute 尚未开放。"
                ),
            },
        )

    run = await start_discovery_run(
        db,
        current_user,
        resume_version_id=payload.resume_version_id,
        limit=payload.limit,
        mode=payload.mode,
        gateway=gateway,
    )
    return build_status_out(run)


@router.get("/discovery/{run_id}", response_model=DiscoveryStatusOut)
async def get_discovery_status_endpoint(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> DiscoveryStatusOut:
    """Poll the status of a discovery run.

    Returns the full per-item progress. Cross-user access returns 404.
    """
    run = get_discovery_run(db, current_user, run_id)
    return build_status_out(run)


@router.post("/discovery/{run_id}/pause", response_model=DiscoveryPauseOut)
async def pause_discovery_endpoint(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> DiscoveryPauseOut:
    """Pause a running discovery run (crash-recovery only in v0.1).

    Since processing runs synchronously inside the start/resume request, pause
    cannot interrupt an in-flight request; it applies to runs that are not
    actively being processed (e.g. a stuck ``running`` run), marking them
    ``paused`` for later resume. Already-terminal runs are a no-op.
    """
    run = pause_discovery_run(db, current_user, run_id)
    result = run.result or {}
    message = result.get("message", "发现任务已暂停")
    return DiscoveryPauseOut(
        run_id=run.id,
        status=result.get("discovery_status", "paused"),
        message=message,
    )


@router.post("/discovery/{run_id}/resume", response_model=DiscoveryStatusOut)
async def resume_discovery_endpoint(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> DiscoveryStatusOut:
    """Resume a paused discovery run.

    Re-scans the visible jobs to rebuild the userscript's in-memory candidate
    cache, then continues processing remaining ``pending`` items. Non-paused
    runs are a no-op (returns the current status).
    """
    run = await resume_discovery_run(db, current_user, run_id, gateway=gateway)
    return build_status_out(run)


__all__ = ["router"]
