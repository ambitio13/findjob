"""Applications router — application records center.

All endpoints are scoped to the current user: created records bind to
``current_user.id``, and list/detail only return records owned by the current
user. Cross-user access returns 404 (not 403) to avoid revealing resource
existence.

This task is manual-first: no external platform side effects are performed
(PRD). The endpoints cover create/list/detail, status updates (enforcing the
state-machine transition table), and manual timeline notes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session
from app.db.models.models import UserProfile
from app.db.repositories import agent_run_repo, generated_artifact_repo
from app.schemas.api import (
    ApplicationCreate,
    ApplicationListOut,
    ApplicationOut,
    ApplicationStatusUpdate,
    ApplicationTimelineCreate,
    PaginatedMeta,
)
from app.schemas.application import (
    ApplicationFailureCategory,
    ApplicationFailureNextAction,
)
from app.schemas.readiness import (
    ReadinessArtifactListOut,
    ReadinessArtifactOut,
    ReadinessArtifactType,
    RunReadinessRunSummary,
    RunReadinessSubmitResponse,
)
from app.services import application_service
from app.services.readiness_service import (
    WORKFLOW_TYPE,
    load_readiness_context,
    persist_application_failure,
)

router = APIRouter(prefix="/applications", tags=["applications"])

#: The four readiness artifact types stored on ``GeneratedArtifact.artifact_type``.
READINESS_ARTIFACT_TYPES = tuple(t.value for t in ReadinessArtifactType)


def _to_out(record) -> ApplicationOut:
    """Project an ``ApplicationRecord`` into ``ApplicationOut``.

    The ``timeline`` column is a JSON list; Pydantic validates each entry into
    an ``ApplicationTimelineEventOut``. A missing/empty timeline degrades to an
    empty list so older rows do not break reads.
    """
    return ApplicationOut.model_validate(record)


@router.get("", response_model=ApplicationListOut)
def list_applications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="Filter by application status."),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationListOut:
    """List the current user's application records, newest first."""
    rows, total = application_service.list_applications(
        db, current_user, page=page, page_size=page_size, status=status
    )
    return ApplicationListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total),
        items=[_to_out(r) for r in rows],
    )


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Create a new application record for an owned job/resume.

    If an active (non-terminal) record already exists for the same job (and,
    when provided, the same resume version), the existing record is returned
    with status 201 so duplicate creation is idempotent from the client's
    perspective (PRD: "returns the existing record").
    """
    record = application_service.create_application(
        db,
        current_user,
        job_id=payload.job_id,
        resume_version_id=payload.resume_version_id,
    )
    return _to_out(record)


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Return one application record (with timeline) for the current user."""
    record = application_service.get_application(db, current_user, application_id)
    return _to_out(record)


@router.patch("/{application_id}/status", response_model=ApplicationOut)
def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Update the application status, enforcing the transition table.

    Returns 404 if the application does not exist or is not owned by the
    current user. Returns 422 if the transition is not allowed or the target
    status is ``preparing`` and the bound resume version has no parsed text.

    When ``status`` is ``failed`` and ``failure`` is supplied, the sanitized
    envelope is persisted on ``latest_error``. When ``agent_run_id`` is
    supplied, it updates ``latest_agent_run_id``.
    """
    record = application_service.update_application_status(
        db,
        current_user,
        application_id,
        new_status=payload.status,
        note=payload.note,
        failure=payload.failure,
        agent_run_id=payload.agent_run_id,
    )
    return _to_out(record)


@router.post("/{application_id}/timeline", response_model=ApplicationOut)
def append_timeline_event(
    application_id: str,
    payload: ApplicationTimelineCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplicationOut:
    """Append a user-note timeline event without changing status.

    Only user-note events may be appended here; status-change events are owned
    by the status-update endpoint so the timeline never claims a transition
    that did not happen (design.md Transaction Rule).
    """
    record = application_service.append_timeline_note(
        db,
        current_user,
        application_id,
        summary=payload.summary,
        metadata=payload.metadata,
    )
    return _to_out(record)


@router.get("/{application_id}/artifacts", response_model=ReadinessArtifactListOut)
def list_application_artifacts(
    application_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ReadinessArtifactListOut:
    """List persisted readiness artifacts for an application, newest first.

    Returns the four readiness artifact types only (``hr_opening_message``,
    ``resume_rewrite_snippet``, ``skill_gap_plan``, ``interview_prep``). The
    binding to an application lives inside the artifact ``source_ids`` JSON
    (key ``application_id``); we scope by the application's ``job_id`` (indexed
    FK) first, then filter on that JSON key so the query stays cheap. JD
    analysis artifacts for the same job are excluded.

    The ``content`` field is the validated structured-output JSON string; the
    frontend re-parses it into the matching output model for rendering.
    """
    record = application_service.get_application(db, current_user, application_id)
    rows = generated_artifact_repo.list_for_application(
        db,
        application_id,
        job_id=record.job_id,
        artifact_types=READINESS_ARTIFACT_TYPES,
    )
    return ReadinessArtifactListOut(
        items=[ReadinessArtifactOut.model_validate(r) for r in rows]
    )


@router.post(
    "/{application_id}/artifacts/{artifact_type}/generate",
    response_model=RunReadinessSubmitResponse,
    status_code=202,
)
async def generate_readiness_artifact(
    application_id: str,
    artifact_type: ReadinessArtifactType,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> RunReadinessSubmitResponse:
    """Submit the readiness artifact generation workflow (enqueue-and-poll).

    Validates ownership + resume-version usability up front (404/422 bubble from
    the context loader before any run is persisted), then creates a ``queued``
    ``AgentRun`` (``workflow_type="readiness_generation"``) with sanitized
    request metadata, enqueues a
    :class:`~app.queue.payloads.ReadinessGenerationPayload` to the worker queue,
    and returns immediately with the run reference. The frontend polls
    ``GET /agent-runs/{run_id}/detail`` until the run reaches a terminal status,
    then hydrates the artifact from the persisted ``GeneratedArtifact`` row.

    Duplicate active-run guard: while an active (``queued``/``running``) run
    exists for the same application + artifact type, the endpoint returns HTTP
    409 instead of enqueuing a second generation. No raw JD or resume text is
    persisted — only IDs and lengths are stored on the run.

    If Redis is unavailable the run is flipped to ``failed`` before returning so
    the user is never left with a silent spinner.
    """
    from app.queue.payloads import ReadinessGenerationPayload
    from app.queue.runtime import enqueue_workflow

    # 1. Validate application/job/resume ownership + data quality (404/422).
    #    This runs BEFORE any run is created so a bad request never leaves a
    #    half-started run behind.
    context, record, job, version, resume = load_readiness_context(
        db, current_user, application_id, artifact_type.value
    )

    # 2. Duplicate active-run guard: reject if a queued/running readiness run
    #    already exists for this application + artifact type. We deliberately
    #    allow re-generation after a run reaches a terminal state.
    runs, _ = agent_run_repo.list_runs_for_user(
        db,
        current_user.id,
        workflow_type=WORKFLOW_TYPE,
        job_id=record.job_id,
        page=1,
        page_size=50,
    )
    for r in runs:
        if r.status not in {"queued", "running"}:
            continue
        r_result = r.result or {}
        if (
            r_result.get("application_id") == application_id
            and r_result.get("artifact_type") == artifact_type.value
        ):
            raise HTTPException(
                status_code=409,
                detail="artifact generation already in progress for this application",
            )

    # 3. Create the durable AgentRun in queued state *before* enqueue so
    #    PostgreSQL stays the source of truth. The result metadata is sanitized:
    #    only IDs + input lengths, no raw text.
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="queued",
        job_id=record.job_id,
        result={
            "user_id": current_user.id,
            "application_id": application_id,
            "job_id": record.job_id,
            "resume_version_id": version.id,
            "resume_id": resume.id,
            "artifact_type": artifact_type.value,
            "source_hash": context.source_hash,
            "jd_raw_len": len(context.job.get("jd_raw") or ""),
            "resume_raw_text_len": len(context.resume.get("raw_text") or ""),
        },
    )
    db.commit()
    db.refresh(run)

    # 4. Enqueue the readiness generation job. If Redis is down, flip the run
    #    to failed so the frontend sees a terminal state instead of polling
    #    forever.
    idempotency_key = f"readiness_generation:{run.id}"
    enqueue_payload = ReadinessGenerationPayload(
        workflow_type=WORKFLOW_TYPE,
        user_id=current_user.id,
        agent_run_id=run.id,
        idempotency_key=idempotency_key,
        application_id=application_id,
        job_id=record.job_id,
        resume_version_id=version.id,
        artifact_type=artifact_type.value,
        source_hash=context.source_hash,
    )
    try:
        await enqueue_workflow(enqueue_payload, job_id=idempotency_key)
    except Exception:
        persist_application_failure(
            db,
            run=run,
            application_id=application_id,
            artifact_type=artifact_type.value,
            error="queue enqueue failed",
            category=ApplicationFailureCategory.queue,
            code="queue_enqueue_failed",
            message="queue enqueue failed",
            retryable=True,
            next_action=ApplicationFailureNextAction.retry,
        )
        db.commit()
        db.refresh(run)

    return RunReadinessSubmitResponse(
        run=RunReadinessRunSummary.model_validate(run),
        application_id=application_id,
        artifact_type=artifact_type,
    )
