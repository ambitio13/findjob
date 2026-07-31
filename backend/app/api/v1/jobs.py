"""Jobs router with manual JD creation, list, and detail.

All endpoints are scoped to the current user: created jobs bind to
``current_user.id``, and list/detail only return records owned by the current
user. Cross-user access returns 404 (not 403) to avoid revealing resource
existence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session, get_model_gateway_dep
from app.db.models.models import JobPosting, UserProfile
from app.db.repositories import agent_run_repo, generated_artifact_repo, job_analysis_repo, job_repo
from app.models_gateway.base import ModelGateway
from app.schemas.api import JobCreate, JobListOut, JobOut, PaginatedMeta
from app.schemas.jd_analysis import (
    GeneratedArtifactOut,
    JdAnalysisModelOutput,
    JobAnalysisDetailOut,
    JobAnalysisListOut,
    JobAnalysisOut,
    RunJdAnalysisRequest,
    RunJdAnalysisResponse,
)
from app.schemas.jd_parse import JdParseRequest, JdParseRunSummary, JdParseSubmitResponse
from app.services.jd_analysis_service import run_resume_aware_jd_analysis

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListOut)
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobListOut:
    rows, total = job_repo.list_for_user(db, current_user.id, page=page, page_size=page_size)
    return JobListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total),
        items=[JobOut.model_validate(r) for r in rows],
    )


@router.post("", response_model=JobOut, status_code=201)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobOut:
    job = job_repo.create(
        db,
        user_id=current_user.id,
        company=payload.company,
        title=payload.title,
        jd_raw=payload.jd_raw,
        platform=payload.platform,
        location=payload.location,
        salary_range=payload.salary_range,
        direction=payload.direction,
        jd_normalized=payload.jd_normalized,
    )
    db.commit()
    db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobOut:
    job = job_repo.get_by_id(db, job_id, current_user.id)
    if job is None:
        # 404 (not 403) to avoid revealing that the resource exists for
        # another user.
        raise HTTPException(status_code=404, detail="job not found")
    return JobOut.model_validate(job)


# ---------------------------------------------------------------------------
# JD paste parsing (parse-then-create flow)
# ---------------------------------------------------------------------------


@router.post("/parse", response_model=JdParseSubmitResponse, status_code=202)
async def parse_job_jd(
    payload: JdParseRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JdParseSubmitResponse:
    """Submit raw JD text for asynchronous parsing (enqueue-and-poll).

    Creates a ``queued`` ``AgentRun`` (``workflow_type="jd_paste_parsing"``)
    with sanitized request metadata, enqueues a ``JdPasteParsePayload`` to the
    worker queue, and returns immediately with the run reference. The frontend
    polls ``GET /agent-runs/{run_id}/detail`` until the run reaches a terminal
    status, then hydrates the parsed fields from ``AgentRun.result.fields``.

    If Redis is unavailable the run is flipped to ``failed`` before returning
    so the user is never left with a silent spinner. The raw JD text is never
    persisted to PostgreSQL — it lives only in the transient queue entry.
    """
    from app.queue.payloads import JdPasteParsePayload
    from app.queue.runtime import enqueue_workflow
    from app.services.jd_parse_service import WORKFLOW_TYPE

    # Create the durable AgentRun in queued state *before* enqueue so
    # PostgreSQL stays the source of truth (design.md queue contract).
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="queued",
        result={
            "user_id": current_user.id,
            "raw_jd_len": len(payload.raw_jd),
            "platform": payload.platform,
        },
    )
    db.commit()
    db.refresh(run)

    # Enqueue the parse job. If Redis is down, flip the run to failed so the
    # frontend sees a terminal state instead of polling forever.
    idempotency_key = f"jd_paste:{run.id}"
    enqueue_payload = JdPasteParsePayload(
        workflow_type="jd_paste_parsing",
        user_id=current_user.id,
        agent_run_id=run.id,
        idempotency_key=idempotency_key,
        raw_jd=payload.raw_jd,
        platform=payload.platform,
    )
    try:
        await enqueue_workflow(enqueue_payload, job_id=idempotency_key)
    except Exception:
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error="queue enqueue failed",
        )
        db.commit()
        db.refresh(run)

    return JdParseSubmitResponse(
        run=JdParseRunSummary.model_validate(run),
        raw_jd=payload.raw_jd,
        platform=payload.platform,
    )


# ---------------------------------------------------------------------------
# Resume-aware JD analysis (Phase 4)
# ---------------------------------------------------------------------------


def _require_owned_job(db: Session, current_user: UserProfile, job_id: str) -> JobPosting:
    """Return the current user's job or raise 404 (not 403)."""
    job = job_repo.get_by_id(db, job_id, current_user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def _list_analyses_for_job(
    db: Session, job_id: str, *, page: int, page_size: int
) -> tuple[list, int]:
    """Return ``(rows, total)`` of ``JobAnalysis`` rows for ``job_id``."""
    return job_analysis_repo.list_for_job(db, job_id, page=page, page_size=page_size)


def _to_detail_out(db: Session, analysis) -> JobAnalysisDetailOut:
    """Project a ``JobAnalysis`` row into a result that can reconstruct the UI.

    Loads the latest ``GeneratedArtifact`` for the run that produced this
    analysis and re-parses its validated JSON ``content`` into ``structured``.
    Rows whose artifact is missing or content cannot be parsed degrade to
    ``artifact=None`` / ``structured=None`` (design.md Compatibility) so older
    or partially-persisted runs do not break the list.
    """
    artifact = None
    structured: JdAnalysisModelOutput | None = None
    if analysis.agent_run_id:
        artifact = generated_artifact_repo.get_latest_for_run(
            db, analysis.agent_run_id, artifact_type="jd_analysis"
        )
    if artifact is not None:
        try:
            structured = JdAnalysisModelOutput.model_validate(json.loads(artifact.content))
        except (ValueError, TypeError):
            # Content was persisted pre-validation or got corrupted. Keep the
            # artifact metadata visible but do not let one bad row break the list.
            structured = None
    return JobAnalysisDetailOut(
        analysis=JobAnalysisOut.model_validate(analysis),
        artifact=GeneratedArtifactOut.model_validate(artifact) if artifact else None,
        structured=structured,
    )


def _agent_run_to_out(run) -> dict:
    """Project an ``AgentRun`` ORM row into the ``agent_run`` response dict.

    Matches the shape of ``AgentRunOut`` (schemas/api.py) so the frontend can
    reuse its existing run-rendering code.
    """
    return {
        "id": run.id,
        "workflow_type": run.workflow_type,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error": run.error,
        "result": run.result,
    }


@router.post("/{job_id}/analyses", response_model=RunJdAnalysisResponse, status_code=201)
async def run_job_analysis(
    job_id: str,
    payload: RunJdAnalysisRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> RunJdAnalysisResponse:
    """Run the resume-aware JD analysis workflow for ``job_id``.

    The route is a thin transport layer: it validates the body, resolves the
    current user, and delegates to the service orchestrator. All workflow
    logic (ownership, model call, persistence) lives in the service. Error
    mapping per design.md §5.1: 404/422 bubble from the loader, 502 for model
    validation failure.
    """
    run, analysis, artifact, execution = await run_resume_aware_jd_analysis(
        db=db,
        current_user=current_user,
        job_id=job_id,
        resume_version_id=payload.resume_version_id,
        gateway=gateway,
    )
    return RunJdAnalysisResponse(
        agent_run=_agent_run_to_out(run),
        analysis=JobAnalysisOut.model_validate(analysis),
        artifact=GeneratedArtifactOut.model_validate(artifact),
        structured=execution.output,
    )


@router.get("/{job_id}/analyses", response_model=JobAnalysisListOut)
def list_job_analyses(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobAnalysisListOut:
    """List prior analyses for the current user's job, newest first.

    Returns ``JobAnalysisDetailOut`` items (analysis + artifact + structured
    output) so the frontend can hydrate the result view from persisted rows
    alone, without relying on the original POST response (R1/R2). Full run
    detail + steps can still be read from ``/agent-runs/{run_id}``.
    """
    _require_owned_job(db, current_user, job_id)
    rows, total = _list_analyses_for_job(db, job_id, page=page, page_size=page_size)
    items = [_to_detail_out(db, r) for r in rows]
    return JobAnalysisListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total).model_dump(),
        items=items,
    )
