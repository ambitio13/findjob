"""Agent runs router with a deterministic JD-analysis smoke endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.orchestrator import manual_jd_analysis_demo
from app.api.deps import get_current_user, get_db_session, get_model_gateway_dep
from app.db.models.models import AgentRun as AgentRunModel
from app.db.models.models import GeneratedArtifact, UserProfile
from app.db.repositories import agent_run_repo
from app.models_gateway.base import ModelGateway
from app.schemas.api import (
    AgentRunDetailOut,
    AgentRunOut,
    AgentStepOut,
    ManualJdAnalysisDemoRequest,
    ManualJdAnalysisDemoResponse,
    PaginatedMeta,
)

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


@router.get("")
def list_agent_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    job_id: str | None = Query(None, description="Scope to a job (finds failed runs too)"),
    workflow_type: str | None = Query(None, description="e.g. resume_aware_jd_analysis"),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> dict:
    rows, total = agent_run_repo.list_runs_for_user(
        db,
        current_user.id,
        page=page,
        page_size=page_size,
        workflow_type=workflow_type,
        job_id=job_id,
    )
    return {
        "meta": PaginatedMeta(page=page, page_size=page_size, total=total),
        "items": [AgentRunOut.model_validate(r) for r in rows],
    }


def _require_owned_run(db: Session, run_id: str, current_user: UserProfile) -> AgentRunModel:
    """Return the current user's run or raise 404 (not 403) (R5)."""
    run = db.get(AgentRunModel, run_id)
    if run is None or run.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="agent run not found")
    return run


@router.get("/{run_id}", response_model=AgentRunOut)
def get_agent_run(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> AgentRunOut:
    run = _require_owned_run(db, run_id, current_user)
    return AgentRunOut.model_validate(run)


@router.get("/{run_id}/detail", response_model=AgentRunDetailOut)
def get_agent_run_detail(
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> AgentRunDetailOut:
    """Return a user-scoped run with its ordered steps (R3/R5).

    The steps carry sanitized metadata only (R6): counts, IDs, provider/model
    /prompt version, validation status, timing, error type/message. Raw prompts,
    resume text, and full model payloads are never stored on steps, so they
    cannot leak here.
    """
    run = _require_owned_run(db, run_id, current_user)
    steps = agent_run_repo.list_steps(db, run.id)
    detail = AgentRunDetailOut.model_validate(run)
    detail.steps = [AgentStepOut.model_validate(s) for s in steps]
    return detail


@router.post("/manual-jd-analysis-demo", response_model=ManualJdAnalysisDemoResponse)
async def manual_jd_analysis_demo_endpoint(
    payload: ManualJdAnalysisDemoRequest,
    gateway: ModelGateway = Depends(get_model_gateway_dep),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ManualJdAnalysisDemoResponse:
    """Deterministic smoke workflow. Persists the run and artifact."""
    run_data, artifact_data = await manual_jd_analysis_demo(payload.jd_text, gateway=gateway)

    run_orm = AgentRunModel(
        id=run_data.id,
        user_id=current_user.id,
        workflow_type=run_data.workflow_type,
        status=run_data.status.value,
        started_at=run_data.started_at,
        finished_at=run_data.finished_at,
        result=run_data.result,
    )
    db.add(run_orm)

    artifact_orm = GeneratedArtifact(
        id=artifact_data.id,
        agent_run_id=artifact_data.agent_run_id,
        artifact_type=artifact_data.artifact_type.value,
        prompt_version=artifact_data.prompt_version,
        model_name=artifact_data.model_name,
        content=artifact_data.content,
        source_ids=artifact_data.source_ids,
    )
    db.add(artifact_orm)
    db.commit()

    return ManualJdAnalysisDemoResponse(
        agent_run=AgentRunOut.model_validate(run_data),
        artifact_id=artifact_data.id,
        artifact_type=artifact_data.artifact_type.value,
        content=artifact_data.content,
    )
