"""BOSS match-decision router.

This router exposes the ``POST /boss/recommended-jobs/{job_id}/match`` endpoint,
which runs the match-decision model against a previously-inspected BOSS
recommended job and persists the result as a ``GeneratedArtifact``.

Flow:

1. Authenticate the user (``X-User-Id``).
2. Load + verify context (job ownership, resume version ownership, raw_text
   non-empty) — 404/422 on failure, no run persisted.
3. Create an ``AgentRun`` (``workflow_type=boss_match_decision``, status=
   ``running``).
4. Drive the 6-step orchestration via ``run_boss_match_decision()``:
   build_prompt → call_model → validate → safety_gate → persist → complete.
5. On model/validation failure: 502, run marked failed.
6. On success: return ``MatchDecisionOut`` with the (possibly gated) decision,
   score, opening_message, and provenance IDs.

Safety invariants:

- The endpoint is **authenticated** (``X-User-Id``).
- It does NOT trigger any browser side effect — it only produces a decision +
  opening message. Browser actions come later in Subtask 6.
- Cross-user access returns 404 (not 403).
- One ``job_id`` per match call — no batch paths.
- Low confidence and validation errors stop before any browser side effect.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session, get_model_gateway_dep
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.models_gateway.base import ModelGateway
from app.schemas.boss_match_decision import (
    MatchDecision,
    MatchDecisionOut,
    MatchJobRequest,
)
from app.services.boss_match_service import run_boss_match_decision

_log = get_logger("app.api.v1.boss_match")

router = APIRouter(prefix="/boss/recommended-jobs", tags=["boss-match"])


@router.post("/{job_id}/match", response_model=MatchDecisionOut)
async def match_job_endpoint(
    job_id: str,
    payload: MatchJobRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> MatchDecisionOut:
    """Run the match-decision model for a BOSS recommended job.

    Takes a ``resume_version_id`` and returns the structured match decision
    (``communicate`` / ``skip`` / ``needs_review``) with score, reasons, risks,
    missing requirements, and an opening message (when applicable).

    The safety gate may downgrade ``communicate`` → ``needs_review`` when the
    score is low, requirements are missing, or the opening message fails
    length/PII/tone validation.
    """
    run, artifact, execution, safety_downgraded, raw_opening_message = (
        await run_boss_match_decision(
            db,
            current_user,
            job_id=job_id,
            resume_version_id=payload.resume_version_id,
            gateway=gateway,
        )
    )

    output = execution.output
    message: str | None = None
    if safety_downgraded:
        message = (
            f"安全检查降级：{output.decision.value}（原决策被安全规则拦截）"
        )

    return MatchDecisionOut(
        decision=MatchDecision(output.decision),
        score=output.score,
        reasons=output.reasons,
        risks=output.risks,
        missing_requirements=output.missing_requirements,
        opening_message=output.opening_message,
        job_id=job_id,
        agent_run_id=run.id,
        artifact_id=artifact.id,
        message=message,
        draft_opening_message=raw_opening_message,
    )
