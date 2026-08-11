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

from app.api.deps import get_current_user, get_db_session
from app.db.models.models import JobPosting, UserProfile
from app.db.repositories import agent_run_repo, generated_artifact_repo, job_analysis_repo, job_repo
from app.schemas.api import (
    JobCreate,
    JobListOut,
    JobOut,
    JobUpdate,
    PaginatedMeta,
    RedFlagSummaryOut,
)
from app.schemas.jd_analysis import (
    GeneratedArtifactOut,
    JdAnalysisModelOutput,
    JobAnalysisDetailOut,
    JobAnalysisListOut,
    JobAnalysisOut,
    RunJdAnalysisRequest,
    RunJdAnalysisRunSummary,
    RunJdAnalysisSubmitResponse,
)
from app.schemas.jd_parse import JdParseRequest, JdParseRunSummary, JdParseSubmitResponse
from app.services.jd_analysis_service import WORKFLOW_TYPE, load_jd_analysis_context

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Editable JobPosting columns accepted by ``PATCH /jobs/{job_id}``. Kept in
#: sync with :class:`JobUpdate` and ``job_repo.update``'s editable set. Used by
#: the PATCH endpoint to filter ``JobUpdate.model_fields_set`` so only
#: client-supplied keys reach the repository.
_EDITABLE_JOB_FIELDS = frozenset(
    {
        "company",
        "title",
        "location",
        "salary_range",
        "direction",
        "platform",
        "jd_raw",
        "source_url",
    }
)


@router.get("", response_model=JobListOut)
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    has_red_flags: bool | None = Query(
        default=None,
        description="风险透视筛选：true 只看最新分析有红旗的岗位，false 只看无红旗的。",
    ),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobListOut:
    """List the user's jobs with the newest analysis' red-flag labels.

    ``has_red_flags`` filters before pagination (so ``meta.total`` stays
    correct): the flagged-id set is derived from each job's newest
    ``JobAnalysis.red_flags`` summary.
    """
    flags_by_job: dict[str, list[dict]] = {}
    id_filter: set[str] | None = None
    exclude_ids: set[str] | None = None
    if has_red_flags is not None:
        flags_by_job = job_analysis_repo.latest_red_flags_by_job(db, current_user.id)
        flagged = {job_id for job_id, flags in flags_by_job.items() if flags}
        if has_red_flags:
            id_filter = flagged
        else:
            exclude_ids = flagged

    rows, total = job_repo.list_for_user(
        db,
        current_user.id,
        page=page,
        page_size=page_size,
        id_filter=id_filter,
        exclude_ids=exclude_ids,
    )

    # Lazily hydrate labels only when the filter did not already load them.
    if has_red_flags is None and rows:
        flags_by_job = job_analysis_repo.latest_red_flags_by_job(
            db, current_user.id, [r.id for r in rows]
        )

    items = [
        JobOut.model_validate(r).model_copy(
            update={
                "red_flags": [
                    RedFlagSummaryOut.model_validate(f)
                    for f in flags_by_job.get(r.id, [])
                ]
            }
        )
        for r in rows
    ]
    return JobListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total),
        items=items,
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
        source_url=payload.source_url,
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


@router.patch("/{job_id}", response_model=JobOut)
def update_job(
    job_id: str,
    payload: JobUpdate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobOut:
    """Edit editable fields of an owned job (company/title/location/salary/
    direction/platform/jd_raw).

    Used to correct a parsed draft or replace the ``(解析中…)`` placeholder
    after an async parse. Only fields the client explicitly sent are applied:
    an omitted field is left untouched, while an explicit ``null`` clears the
    column (location/salary_range/direction are nullable). This distinction is
    made via ``JobUpdate.model_fields_set`` so a partial JSON body like
    ``{"location": null}`` clears the city without touching the others.
    """
    job = job_repo.get_by_id(db, job_id, current_user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    # Only pass through the keys the client actually sent. ``model_fields_set``
    # is the set of fields present in the request body (including ones set to
    # null), so an explicit null clears the column while an omitted key is a
    # no-op. Without this, passing all model attributes would overwrite every
    # field with the schema defaults (None) on every PATCH.
    supplied = {
        key: getattr(payload, key)
        for key in payload.model_fields_set
        if key in _EDITABLE_JOB_FIELDS
    }
    job_repo.update(db, job, **supplied)
    db.commit()
    db.refresh(job)
    return JobOut.model_validate(job)


# ---------------------------------------------------------------------------
# JD paste parsing (create-job-first async flow)
# ---------------------------------------------------------------------------


#: Placeholder company/title shown in the job list while an async parse is in
#: flight. The worker overwrites these with parsed values on success; the user
#: can also edit them via ``PATCH /jobs/{job_id}`` once the run completes.
_PARSE_INFLIGHT_PLACEHOLDER = "(解析中…)"


@router.post("/parse", response_model=JdParseSubmitResponse, status_code=202)
async def parse_job_jd(
    payload: JdParseRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JdParseSubmitResponse:
    """Submit raw JD text for asynchronous parsing (create-job-first).

    Creates a ``JobPosting`` row up front (company/title set to a
    ``"(解析中…)"`` placeholder, ``jd_raw`` persisted) so the job appears in the
    list immediately and the frontend can close the create modal without
    blocking. A ``queued`` ``AgentRun`` (``workflow_type="jd_paste_parsing"``)
    is created and linked to the job, then a ``JdPasteParsePayload`` is
    enqueued to the worker queue. The worker writes the parsed fields back to
    ``job.jd_normalized`` and overwrites the placeholder company/title on
    success, so the list refreshes naturally on the next poll.

    If Redis is unavailable the run is flipped to ``failed`` before returning
    so the user is never left with a silent spinner — the placeholder job
    remains so the user can edit it manually. The raw JD text is persisted on
    the job row (not on the AgentRun metadata) for later editing.
    """
    from app.queue.payloads import JdPasteParsePayload
    from app.queue.runtime import enqueue_workflow
    from app.services.jd_parse_service import WORKFLOW_TYPE

    # 1. Create the JobPosting up front with a placeholder so it shows in the
    #    list immediately. The worker overwrites company/title/jd_normalized
    #    on success; the user can also PATCH it afterwards.
    platform = payload.platform or "manual"
    job = job_repo.create(
        db,
        user_id=current_user.id,
        company=_PARSE_INFLIGHT_PLACEHOLDER,
        title=_PARSE_INFLIGHT_PLACEHOLDER,
        jd_raw=payload.raw_jd,
        platform=platform,
        source_url=payload.source_url,
    )

    # 2. Create the durable AgentRun in queued state *before* enqueue so
    #    PostgreSQL stays the source of truth (design.md queue contract). The
    #    run is linked to the job so the frontend can discover it from the job
    #    list/detail and poll for completion.
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="queued",
        job_id=job.id,
        result={
            "user_id": current_user.id,
            "job_id": job.id,
            "raw_jd_len": len(payload.raw_jd),
            "platform": payload.platform,
        },
    )
    db.commit()
    db.refresh(job)
    db.refresh(run)

    # 3. Enqueue the parse job. If Redis is down, flip the run to failed so
    #    the frontend sees a terminal state instead of polling forever. The
    #    placeholder job remains so the user can still edit it manually.
    idempotency_key = f"jd_paste:{run.id}"
    enqueue_payload = JdPasteParsePayload(
        workflow_type="jd_paste_parsing",
        user_id=current_user.id,
        agent_run_id=run.id,
        idempotency_key=idempotency_key,
        raw_jd=payload.raw_jd,
        platform=payload.platform,
        job_id=job.id,
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
        job=JobOut.model_validate(job),
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


@router.post(
    "/{job_id}/analyses",
    response_model=RunJdAnalysisSubmitResponse,
    status_code=202,
)
async def run_job_analysis(
    job_id: str,
    payload: RunJdAnalysisRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> RunJdAnalysisSubmitResponse:
    """Submit the resume-aware JD analysis workflow for ``job_id`` (enqueue-and-poll).

    Validates ownership + resume-version usability up front (404/422 bubble
    from the context loader before any run is persisted), then creates a
    ``queued`` ``AgentRun`` (``workflow_type="resume_aware_jd_analysis"``)
    with sanitized request metadata, enqueues a
    :class:`~app.queue.payloads.ResumeAwareJdAnalysisPayload` to the worker
    queue, and returns immediately with the run reference. The frontend polls
    ``GET /agent-runs/{run_id}/detail`` until the run reaches a terminal
    status, then hydrates the analysis from the persisted ``JobAnalysis`` /
    ``GeneratedArtifact`` rows.

    Duplicate submit guard: while an active (``queued``/``running``) run exists
    for the same job, the endpoint returns HTTP 409 instead of enqueuing a
    second analysis. No raw JD or resume text is persisted — only IDs and
    lengths are stored on the run.

    If Redis is unavailable the run is flipped to ``failed`` before returning
    so the user is never left with a silent spinner.
    """
    from app.queue.payloads import ResumeAwareJdAnalysisPayload
    from app.queue.runtime import enqueue_workflow

    # 1. Validate job ownership (404 on missing/cross-user).
    _require_owned_job(db, current_user, job_id)

    # 2. Validate the resume version is owned + has usable text. This raises
    #    404/422 directly and must run BEFORE any run is created so a bad
    #    request never leaves a half-started run behind.
    context = load_jd_analysis_context(db, current_user, job_id, payload.resume_version_id)

    # 3. Duplicate active-run guard: reject if a queued/running analysis run
    #    already exists for this job. This prevents the user from stacking
    #    concurrent analyses while one is in flight (design.md "Duplicate
    #    Submit" MVP). We deliberately allow re-analysis after a run reaches a
    #    terminal state (succeeded/failed).
    runs, _ = agent_run_repo.list_runs_for_user(
        db,
        current_user.id,
        workflow_type=WORKFLOW_TYPE,
        job_id=job_id,
        page=1,
        page_size=50,
    )
    if any(r.status in {"queued", "running"} for r in runs):
        raise HTTPException(status_code=409, detail="analysis already in progress for this job")

    # 4. Create the durable AgentRun in queued state *before* enqueue so
    #    PostgreSQL stays the source of truth (design.md queue contract). The
    #    result metadata is sanitized: only IDs + input lengths, no raw text.
    resume_id = str(context.resume.get("resume_id") or "")
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="queued",
        job_id=job_id,
        result={
            "user_id": current_user.id,
            "job_id": job_id,
            "resume_version_id": payload.resume_version_id,
            "resume_id": resume_id,
            "jd_raw_len": len(context.job.get("jd_raw") or ""),
            "resume_raw_text_len": len(context.resume.get("raw_text") or ""),
        },
    )
    db.commit()
    db.refresh(run)

    # 5. Enqueue the analysis job. If Redis is down, flip the run to failed so
    #    the frontend sees a terminal state instead of polling forever.
    #
    #    Capture the readiness-style source hash at enqueue time (design.md
    #    §H2). The worker recomputes it before model execution; on mismatch the
    #    run fails with ``code = "stale_source"`` so an analysis never proceeds
    #    against stale job/resume/profile data.
    from app.services.jd_analysis_service import compute_enqueue_source_hash

    enqueue_source_hash = compute_enqueue_source_hash(
        db=db,
        current_user=current_user,
        job_id=job_id,
        resume_version_id=payload.resume_version_id,
    )
    idempotency_key = f"jd_analysis:{run.id}"
    enqueue_payload = ResumeAwareJdAnalysisPayload(
        workflow_type=WORKFLOW_TYPE,
        user_id=current_user.id,
        agent_run_id=run.id,
        idempotency_key=idempotency_key,
        job_id=job_id,
        resume_version_id=payload.resume_version_id,
        source_hash=enqueue_source_hash,
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

    return RunJdAnalysisSubmitResponse(
        run=RunJdAnalysisRunSummary.model_validate(run),
        resume_version_id=payload.resume_version_id,
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
