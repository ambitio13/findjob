"""Agent runs router with a deterministic JD-analysis smoke endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import manual_jd_analysis_demo
from app.api.deps import get_db_session, get_model_gateway_dep
from app.db.models.models import AgentRun as AgentRunModel
from app.db.models.models import GeneratedArtifact
from app.models_gateway.base import ModelGateway
from app.schemas.api import (
    AgentRunOut,
    ManualJdAnalysisDemoRequest,
    ManualJdAnalysisDemoResponse,
    PaginatedMeta,
)

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


@router.get("")
def list_agent_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
) -> dict:
    offset = (page - 1) * page_size
    rows = (
        db.execute(
            select(AgentRunModel)
            .order_by(AgentRunModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    total = db.execute(select(AgentRunModel.id)).all()
    return {
        "meta": PaginatedMeta(page=page, page_size=page_size, total=len(total)),
        "items": [AgentRunOut.model_validate(r) for r in rows],
    }


@router.get("/{run_id}", response_model=AgentRunOut)
def get_agent_run(run_id: str, db: Session = Depends(get_db_session)) -> AgentRunOut:
    run = db.get(AgentRunModel, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="agent run not found")
    return AgentRunOut.model_validate(run)


@router.post("/manual-jd-analysis-demo", response_model=ManualJdAnalysisDemoResponse)
async def manual_jd_analysis_demo_endpoint(
    payload: ManualJdAnalysisDemoRequest,
    gateway: ModelGateway = Depends(get_model_gateway_dep),
    db: Session = Depends(get_db_session),
) -> ManualJdAnalysisDemoResponse:
    """Deterministic smoke workflow. Persists the run and artifact."""
    run_data, artifact_data = await manual_jd_analysis_demo(payload.jd_text, gateway=gateway)

    run_orm = AgentRunModel(
        id=run_data.id,
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
