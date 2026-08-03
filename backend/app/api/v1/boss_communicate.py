"""BOSS immediate-communicate router.

Exposes two endpoints under ``/boss/recommended-jobs/{job_id}/communicate/...``:

1. ``POST /prepare`` — draft a ``boss_immediate_communicate`` action from a
   match-decision artifact. Synchronous, no browser side effect.
2. ``POST /{action_id}/execute`` — run the approval + idempotency guards, then
   invoke the browser adapter to click "立即沟通" and send the opening message.

Safety invariants:

- Both endpoints are authenticated (``X-User-Id``).
- Cross-user access returns 404 (not 403).
- One ``job_id`` per call — no batch paths.
- The prepare endpoint does NOT trigger any browser side effect. It only
  drafts an approval-bound action.
- The execute endpoint is blocked by ``assert_action_approved`` until the user
  approves the exact payload. A blocked execute returns 409.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.schemas.application_action import ApplicationActionOut
from app.schemas.boss_communicate import (
    CommunicateExecuteOut,
    CommunicateExecuteRequest,
    CommunicatePrepareOut,
    CommunicatePrepareRequest,
)
from app.services.boss_communicate_service import (
    BossCommunicateBlockedError,
    prepare_communicate_action,
    run_boss_communicate_execute,
)

_log = get_logger("app.api.v1.boss_communicate")

router = APIRouter(prefix="/boss/recommended-jobs", tags=["boss-communicate"])


@router.post(
    "/{job_id}/communicate/prepare",
    response_model=CommunicatePrepareOut,
    status_code=201,
)
async def prepare_communicate(
    job_id: str,
    payload: CommunicatePrepareRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> CommunicatePrepareOut:
    """Draft a ``boss_immediate_communicate`` action from a match-decision artifact.

    Reads the ``boss_match_decision`` artifact, asserts the decision is
    ``communicate`` with a valid opening message, and creates an
    ``ApplicationAction`` in ``approval_required`` status bound to a payload hash
    and external idempotency key. No browser side effect is performed.
    """
    action = prepare_communicate_action(
        db,
        current_user,
        job_id,
        resume_version_id=payload.resume_version_id,
        match_artifact_id=payload.match_artifact_id,
    )
    return CommunicatePrepareOut(
        action=ApplicationActionOut.model_validate(action),
        message="沟通动作已草拟，等待用户审批",
    )


@router.post(
    "/{job_id}/communicate/{action_id}/execute",
    response_model=CommunicateExecuteOut,
)
async def execute_communicate(
    job_id: str,
    action_id: str,
    payload: CommunicateExecuteRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> CommunicateExecuteOut:
    """Run the approval + idempotency guards for a communicate execute.

    The action must be approved (via ``POST /applications/{application_id}/
    actions/{action_id}/approve``) before this endpoint will proceed. A blocked
    execute returns 409 with ``detail.reason`` explaining why.

    After the guards pass, the browser adapter is invoked to click "立即沟通"
    and send the opening message. The response carries the terminal result.
    """
    try:
        record, action, replayed = await run_boss_communicate_execute(
            db,
            current_user=current_user,
            job_id=job_id,
            application_id=payload.application_id,
            action_id=action_id,
        )
    except BossCommunicateBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "message": exc.message,
                "action_id": exc.action_id,
            },
        ) from exc

    if replayed:
        message = f"幂等重放: 立即沟通已完成: {action.external_result_status}"
    elif action.external_result_status is not None:
        message = f"立即沟通已完成: {action.external_result_status}"
    else:
        message = "审批与幂等检查通过，准备执行立即沟通"

    return CommunicateExecuteOut(
        action=ApplicationActionOut.model_validate(action),
        message=message,
    )
