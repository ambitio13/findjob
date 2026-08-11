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
from app.db.repositories import (
    agent_run_repo,
    follow_up_suggestion_repo,
    generated_artifact_repo,
)
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
from app.schemas.application_action import ApplicationActionOut
from app.schemas.followup import (
    FollowUpScanOut,
    FollowUpSuggestionListOut,
    FollowUpSuggestionOut,
    SuggestionStatus,
)
from app.schemas.outcome import OutcomeCreate, OutcomeListOut, OutcomeOut
from app.schemas.platform_submission import (
    PlatformSubmissionAbortResponse,
    PlatformSubmissionPrepareRequest,
    PlatformSubmissionPrepareResponse,
    PlatformSubmissionSubmitResponse,
)
from app.schemas.readiness import (
    ReadinessArtifactListOut,
    ReadinessArtifactOut,
    ReadinessArtifactType,
    RunReadinessRunSummary,
    RunReadinessSubmitResponse,
)
from app.services import application_service, followup_service, outcome_service
from app.services.platform_submission_service import (
    WORKFLOW_TYPE as PLATFORM_PREPARE_WORKFLOW_TYPE,
)
from app.services.platform_submission_service import (
    PlatformSubmitBlockedError,
    abort_platform_submit,
    load_prepare_context,
    run_platform_guided_submit_submit,
)
from app.services.readiness_service import (
    WORKFLOW_TYPE,
    load_readiness_context,
    persist_application_failure,
)

router = APIRouter(prefix="/applications", tags=["applications"])

#: The four readiness artifact types stored on ``GeneratedArtifact.artifact_type``.
READINESS_ARTIFACT_TYPES = tuple(t.value for t in ReadinessArtifactType)


def _to_out(record, *, is_duplicate: bool = False) -> ApplicationOut:
    """Project an ``ApplicationRecord`` into ``ApplicationOut``.

    The ``timeline`` column is a JSON list; Pydantic validates each entry into
    an ``ApplicationTimelineEventOut``. A missing/empty timeline degrades to an
    empty list so older rows do not break reads. ``is_duplicate`` is set only
    on the create response when a duplicate was returned instead of a new row.
    """
    out = ApplicationOut.model_validate(record)
    out.is_duplicate = is_duplicate
    return out


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
    record, is_new = application_service.create_application(
        db,
        current_user,
        job_id=payload.job_id,
        resume_version_id=payload.resume_version_id,
    )
    return _to_out(record, is_duplicate=not is_new)


# --- Follow-up suggestions (Phase 5) ---------------------------------------
# Registered BEFORE the ``/{application_id}`` routes so the static segments
# win route matching. Suggestions are advisory only: acting on one always
# re-enters the existing generation + approval flow.


@router.get("/follow-up-suggestions", response_model=FollowUpSuggestionListOut)
def list_follow_up_suggestions(
    status: SuggestionStatus | None = Query(
        None, description="Filter by suggestion status (defaults to all)."
    ),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> FollowUpSuggestionListOut:
    """List the current user's follow-up suggestions, newest first."""
    rows = follow_up_suggestion_repo.list_for_user(
        db, current_user.id, status=status.value if status else None
    )
    return FollowUpSuggestionListOut(
        items=[FollowUpSuggestionOut.model_validate(r) for r in rows]
    )


@router.post("/follow-up-scan", response_model=FollowUpScanOut)
def run_follow_up_scan(
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> FollowUpScanOut:
    """Run the follow-up scan for the current user right now.

    The scheduler runs the same scan daily; this endpoint gives the user an
    on-demand trigger. Creates advisory suggestions and (when enough outcome
    evidence exists) a new match-threshold calibration.
    """
    created, threshold, calibrated_now = followup_service.run_follow_up_scan(
        db, current_user
    )
    return FollowUpScanOut(
        created=len(created),
        suggestions=[FollowUpSuggestionOut.model_validate(r) for r in created],
        active_threshold=threshold,
        calibrated_now=calibrated_now,
    )


@router.post(
    "/follow-up-suggestions/{suggestion_id}/dismiss",
    response_model=FollowUpSuggestionOut,
)
def dismiss_follow_up_suggestion(
    suggestion_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> FollowUpSuggestionOut:
    """Dismiss a pending suggestion (the user disagrees or already handled it)."""
    row = followup_service.resolve_suggestion(
        db, current_user, suggestion_id, target=SuggestionStatus.dismissed
    )
    return FollowUpSuggestionOut.model_validate(row)


@router.post(
    "/follow-up-suggestions/{suggestion_id}/action",
    response_model=FollowUpSuggestionOut,
)
def action_follow_up_suggestion(
    suggestion_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> FollowUpSuggestionOut:
    """Mark a pending suggestion as actioned.

    The caller confirms they started acting on it (e.g. regenerated the
    opening message). The endpoint itself performs no external action.
    """
    row = followup_service.resolve_suggestion(
        db, current_user, suggestion_id, target=SuggestionStatus.actioned
    )
    return FollowUpSuggestionOut.model_validate(row)


# --- Single-application routes ----------------------------------------------


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


@router.post(
    "/{application_id}/outcomes",
    response_model=OutcomeOut,
    status_code=201,
)
def record_outcome(
    application_id: str,
    payload: OutcomeCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> OutcomeOut:
    """Record what actually happened after a submission (feedback loop).

    Appends an outcome event (replied / rejected / interview / offer) and a
    matching timeline entry. When the state machine allows it, the record's
    status follows the outcome (``interview``/``offer`` → ``interviewing``,
    ``rejected`` → ``rejected``); otherwise the outcome is stored as evidence
    without forcing an invalid transition.

    Returns 404 when the application does not exist or is not owned by the
    current user.
    """
    outcome, _ = outcome_service.record_outcome(db, current_user, application_id, payload)
    return OutcomeOut.model_validate(outcome)


@router.get("/{application_id}/outcomes", response_model=OutcomeListOut)
def list_outcomes(
    application_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> OutcomeListOut:
    """List outcome events for one application, oldest first."""
    rows = outcome_service.list_outcomes(db, current_user, application_id)
    return OutcomeListOut(items=[OutcomeOut.model_validate(r) for r in rows])


@router.get("/{application_id}/artifacts", response_model=ReadinessArtifactListOut)
def list_application_artifacts(
    application_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ReadinessArtifactListOut:
    """List persisted readiness artifacts for an application, newest first.

    Returns the readiness artifact types only (``hr_opening_message``,
    ``resume_rewrite_snippet``, ``skill_gap_plan``, ``interview_prep``,
    ``targeted_resume``). The
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


@router.post(
    "/{application_id}/platform-submissions/prepare",
    response_model=PlatformSubmissionPrepareResponse,
    status_code=202,
)
async def prepare_platform_submission(
    application_id: str,
    body: PlatformSubmissionPrepareRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> PlatformSubmissionPrepareResponse:
    """Submit the platform guided-submit *prepare* workflow (enqueue-and-poll).

    Validates application ownership + status (must be ``materials_ready`` or
    ``approval_required``) + resume-version usability up front (404/422 bubble
    from the context loader before any run is persisted), then creates a
    ``queued`` ``AgentRun`` (``workflow_type="platform_guided_submit_prepare"``)
    with sanitized request metadata, enqueues a
    :class:`~app.queue.payloads.PlatformGuidedSubmitPreparePayload` to the worker
    queue, and returns immediately with the run reference. The frontend polls
    ``GET /agent-runs/{run_id}/detail`` until the run reaches a terminal status,
    then hydrates the prepared action from the persisted ``ApplicationAction``.

    The worker runs the BOSS adapter in dry-run/fill-only mode and never
    performs the final submit. No raw JD, resume text, cookies, tokens, or
    session data crosses the queue boundary — only durable resource IDs and the
    ``source_hash`` captured at enqueue time.

    Duplicate active-run guard: while an active (``queued``/``running``) prepare
    run exists for this application, the endpoint returns HTTP 409 instead of
    enqueuing a second prepare.

    If Redis is unavailable the run is flipped to ``failed`` before returning so
    the user is never left with a silent spinner.
    """
    from app.queue.payloads import PlatformGuidedSubmitPreparePayload
    from app.queue.runtime import enqueue_workflow

    # 1. Validate application/job/resume ownership + status + data quality
    #    (404/422). This runs BEFORE any run is created so a bad request never
    #    leaves a half-started run behind.
    record, job, version, source_hash = load_prepare_context(
        db, current_user, application_id
    )

    # 2. Duplicate active-run guard: reject if a queued/running prepare run
    #    already exists for this application. We deliberately allow a re-prepare
    #    after a run reaches a terminal state.
    runs, _ = agent_run_repo.list_runs_for_user(
        db,
        current_user.id,
        workflow_type=PLATFORM_PREPARE_WORKFLOW_TYPE,
        job_id=record.job_id,
        page=1,
        page_size=50,
    )
    for r in runs:
        if r.status not in {"queued", "running"}:
            continue
        r_result = r.result or {}
        if r_result.get("application_id") == application_id:
            raise HTTPException(
                status_code=409,
                detail="platform submission prepare already in progress for this application",
            )

    # 3. Create the durable AgentRun in queued state *before* enqueue so
    #    PostgreSQL stays the source of truth. The result metadata is sanitized:
    #    only IDs + the target resource + source hash, no raw text.
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=PLATFORM_PREPARE_WORKFLOW_TYPE,
        status="queued",
        job_id=record.job_id,
        result={
            "user_id": current_user.id,
            "application_id": application_id,
            "job_id": record.job_id,
            "resume_version_id": version.id,
            "source_hash": source_hash,
            "target_platform": "boss",
            "target_resource": body.target_resource,
            "selected_artifact_ids": list(body.selected_artifact_ids),
            "mode": "dry_run",
        },
    )
    db.commit()
    db.refresh(run)

    # 4. Enqueue the platform guided-submit prepare job. If Redis is down, flip
    #    the run to failed so the frontend sees a terminal state instead of
    #    polling forever.
    idempotency_key = f"platform_guided_submit_prepare:{run.id}"
    enqueue_payload = PlatformGuidedSubmitPreparePayload(
        workflow_type=PLATFORM_PREPARE_WORKFLOW_TYPE,
        user_id=current_user.id,
        agent_run_id=run.id,
        idempotency_key=idempotency_key,
        application_id=application_id,
        job_id=record.job_id,
        resume_version_id=version.id,
        source_hash=source_hash,
        selected_artifact_ids=list(body.selected_artifact_ids),
        outgoing_text=body.outgoing_text,
        resume_file_reference=body.resume_file_reference,
        target_resource=body.target_resource,
    )
    try:
        await enqueue_workflow(enqueue_payload, job_id=idempotency_key)
    except Exception:
        persist_application_failure(
            db,
            run=run,
            application_id=application_id,
            artifact_type="platform_submit",
            error="queue enqueue failed",
            category=ApplicationFailureCategory.queue,
            code="queue_enqueue_failed",
            message="queue enqueue failed",
            retryable=True,
            next_action=ApplicationFailureNextAction.retry,
        )
        db.commit()
        db.refresh(run)

    return PlatformSubmissionPrepareResponse(
        run=RunReadinessRunSummary.model_validate(run),
        application_id=application_id,
        target_platform="boss",
        mode="dry_run",
    )


@router.post(
    "/{application_id}/platform-submissions/{run_id}/submit",
    response_model=PlatformSubmissionSubmitResponse,
)
async def submit_platform_submission(
    application_id: str,
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> PlatformSubmissionSubmitResponse:
    """Final platform submit behind the approval + idempotency guards.

    Runs synchronously inside the request (not a queue worker) because the
    adapter final submit is short-lived and the user is waiting for the result.
    The endpoint:

    1. Loads + verifies ownership + that the run is the succeeded prepare run
       for this application (404/409).
    2. Recomputes the current payload hash + source hash and calls
       ``assert_action_approved`` — the hard approval boundary.
    3. Checks the external idempotency key; if a terminal result already exists
       it is returned without touching the platform (design.md §H1).
    4. Calls ``adapter.submit_prepared`` — the only external side effect.
    5. Persists the sanitized terminal result (``submitted`` /
       ``duplicate_detected`` / ``unknown`` / ``platform_failure``).

    If the approval guard blocks execution (action not approved, payload hash
    mismatch, or source hash mismatch) the endpoint returns HTTP 409 so the
    frontend can prompt the user to re-approve. No cookies, tokens, credentials,
    raw JD, raw resume, or page HTML are persisted.
    """
    try:
        _, run, action = await run_platform_guided_submit_submit(
            db,
            current_user=current_user,
            application_id=application_id,
            run_id=run_id,
        )
    except PlatformSubmitBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "message": exc.message,
                "action_id": exc.action_id,
            },
        ) from exc

    return PlatformSubmissionSubmitResponse(
        run=RunReadinessRunSummary.model_validate(run),
        application_id=application_id,
        action=ApplicationActionOut.model_validate(action),
    )


@router.post(
    "/{application_id}/platform-submissions/{run_id}/abort",
    response_model=PlatformSubmissionAbortResponse,
)
async def abort_platform_submission(
    application_id: str,
    run_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> PlatformSubmissionAbortResponse:
    """Abort an in-flight platform final submit (side-effect-free cancellation).

    Aborting revokes the prepared ``ApplicationAction`` so it can no longer
    authorize execution and appends a ``platform_submit_blocked`` timeline event
    noting the user-initiated abort. No platform side effect is performed.

    If the adapter already produced a terminal result (``submitted`` /
    ``duplicate_detected`` / ``unknown`` / ``platform_failure``) the abort is
    refused with HTTP 409 — the result stands and must be reconciled manually.
    If the external submit is in-flight (``external_started_at`` set, no
    terminal result) the abort is also refused with 409 because the browser
    action cannot be safely cancelled.
    """
    _, run, action = abort_platform_submit(
        db,
        current_user=current_user,
        application_id=application_id,
        run_id=run_id,
    )
    return PlatformSubmissionAbortResponse(
        run=RunReadinessRunSummary.model_validate(run),
        application_id=application_id,
        action=ApplicationActionOut.model_validate(action),
    )
