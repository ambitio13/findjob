"""Platform guided-submit service — prepare (dry-run fill) workflow.

This module owns the *prepare* phase of the platform guided-submit pilot
(design.md §Architecture). It is the queue worker entry point that:

- re-loads + ownership-checks the application/job/resume version/profile;
- re-checks the source snapshot for staleness between enqueue and execution;
- runs the BOSS adapter in dry-run/fill-only mode via the typed
  :class:`~app.platforms.base.PlatformAdapter` boundary (never the final submit
  action);
- on a ``filled_preview`` outcome, persists a sanitized
  :class:`FilledSubmissionSnapshot`, creates/updates a ``platform_submit``
  :class:`ApplicationAction` whose ``payload_hash`` is computed from the exact
  filled snapshot, and transitions the application to ``approval_required``;
- on any non-filled outcome, persists a sanitized
  :class:`ApplicationFailureEnvelope` (category ``platform``) and the matching
  next-action from the design.md failure matrix, without retrying blindly.

The adapter is selected via :func:`app.platforms.boss.registry.get_adapter`
(fake by default; the real Playwright adapter is enabled only behind
``BOSS_ADAPTER_ENABLED``). Product services never contain selectors or DOM
traversal — they call the adapter through the typed boundary.

This task does **not** perform the final submit. That path lives behind the
approval + idempotency guards in a later step (design.md §Step 5).

No cookies, tokens, credentials, raw JD, raw resume, or page HTML may appear in
any persisted result. The :class:`FilledSubmissionSnapshot` is sanitized at the
adapter boundary; the failure envelope builder drops sensitive source-id keys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.models.models import (
    ApplicationAction,
    ApplicationRecord,
    JobPosting,
    ResumeVersion,
    UserProfile,
)
from app.db.repositories import (
    agent_run_repo,
    application_action_repo,
    application_repo,
)
from app.db.repositories.agent_run_repo import AgentRun
from app.platforms.base import (
    PREPARE_FAILURE_CODES,
    SUBMIT_FAILURE_CODES,
    FilledSubmissionSnapshot,
    PrepareContext,
    PrepareOutcome,
    PrepareResult,
    SubmitContext,
    SubmitOutcome,
    SubmitResult,
    prepare_failure_code,
)
from app.schemas.application import (
    ApplicationFailureCategory,
    ApplicationFailureNextAction,
    ApplicationStatus,
)
from app.schemas.application_action import (
    ApplicationActionOut,
    ApplicationActionPreview,
    ApplicationActionSourceSnapshot,
    ApprovalBlockedError,
    ExternalActionResult,
    ExternalActionResultStatus,
    ExternalActionStatus,
    ExternalActionType,
)
from app.services.application_state import (
    InvalidTransitionError,
    assert_transition,
    build_failure_envelope,
    build_source_snapshot,
)
from app.services.approval_boundary import (
    assert_action_approved,
    compute_external_idempotency_key,
    compute_payload_hash,
)

_log = get_logger("app.services.platform_submission_service")

#: The fixed workflow type stored on ``AgentRun.workflow_type``.
WORKFLOW_TYPE = "platform_guided_submit_prepare"

#: Fixed step names produced by the prepare workflow (design.md §Observability).
_STEP_LOAD_CONTEXT = "load_application_context"
_STEP_CHECK_ENTRY_GUARDS = "check_entry_guards"
_STEP_OPEN_PLATFORM_SESSION = "open_platform_session"
_STEP_NAVIGATE = "navigate_to_application_form"
_STEP_CLASSIFY_PAGE = "classify_page_state"
_STEP_FILL_FORM = "fill_form"
_STEP_CAPTURE_SNAPSHOT = "capture_filled_snapshot"
_STEP_CREATE_APPROVAL_ACTION = "create_approval_action"
_STEP_SUBMIT_FINAL = "submit_final"
_STEP_RECORD_RESULT = "record_result"

#: Application statuses from which a prepare may be requested (design.md §State
#: Machine Mapping).
_PREPARABLE_STATUSES = frozenset(
    {ApplicationStatus.materials_ready.value, ApplicationStatus.approval_required.value}
)

#: Application statuses from which a final submit may be requested. The
#: application must have been approved by the user (design.md §State Machine
#: Mapping). ``submitted`` and ``failed`` are also admitted so that a repeat
#: submit request after a confirmed/failed result is handled by the idempotency
#: guard (returning the existing terminal result) rather than rejected at the
#: status gate — this keeps the submit endpoint idempotent (design.md §H1).
_SUBMITTABLE_STATUSES = frozenset(
    {
        ApplicationStatus.approved.value,
        ApplicationStatus.submitted.value,
        ApplicationStatus.failed.value,
    }
)

#: Prompt versions bound into the prepare source snapshot. The prepare flow does
#: not call a model, so only the readiness prompt version is recorded for
#: staleness parity with readiness artifacts.
_PROMPT_VERSIONS: dict[str, str] = {"readiness": "pilot"}


def compute_prepare_source_hash(
    *, job: JobPosting, version: ResumeVersion, profile: UserProfile
) -> str:
    """Compute the current source hash for the prepare flow.

    Mirrors :func:`readiness_service._compute_source_hash` so the prepare flow
    shares the same freshness contract as readiness artifacts. The hash covers
    job/resume/profile metadata — never raw text.
    """
    snapshot = build_source_snapshot(
        job_id=job.id,
        job_updated_at=job.updated_at,
        resume_version_id=version.id,
        resume_version_no=version.version_no,
        profile_updated_at=profile.updated_at,
        prompt_versions=_PROMPT_VERSIONS,
    )
    return snapshot.source_hash


def load_prepare_context(
    db,
    current_user: UserProfile,
    application_id: str,
) -> tuple[ApplicationRecord, JobPosting, ResumeVersion, str]:
    """Load + verify ownership for a prepare request.

    Raises ``HTTPException`` (404/422) on ownership or data-quality failures.
    Returns ``(record, job, version, source_hash)`` so the API endpoint can
    build sanitized run metadata without re-loading rows.

    The application status must be ``materials_ready`` or ``approval_required``
    (design.md §State Machine Mapping); any other status raises 422.
    """
    from fastapi import HTTPException

    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "platform.application_not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")

    if record.status not in _PREPARABLE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                "application status must be materials_ready or approval_required "
                f"to prepare a platform submission (got {record.status})"
            ),
        )

    job = db.get(JobPosting, record.job_id)
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="application not found")

    resume_version_id = record.resume_version_id
    if resume_version_id is None:
        raise HTTPException(
            status_code=422,
            detail="application has no resume version bound",
        )

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="application not found")

    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=422, detail="resume version has no parsed text")

    source_hash = compute_prepare_source_hash(
        job=job, version=version, profile=current_user
    )
    return record, job, version, source_hash


def _action_preview_from_snapshot(
    snapshot: FilledSubmissionSnapshot,
) -> ApplicationActionPreview:
    """Build the approval-boundary preview from a filled snapshot.

    The preview carries exactly the fields the user is approving — never secrets,
    cookies, tokens, or session data. ``outgoing_text`` is the concatenation of
    filled field values the user sees (design.md §Payload Hash), so the payload
    hash binds to the exact filled text.
    """
    outgoing_parts = [
        f.value for f in snapshot.fields if f.value
    ]
    outgoing_text = "\n".join(outgoing_parts) if outgoing_parts else None
    return ApplicationActionPreview(
        action_type=ExternalActionType.platform_submit,
        target_platform=snapshot.target_platform,
        target_resource=snapshot.target_resource,
        selected_artifact_ids=list(snapshot.selected_artifact_ids),
        outgoing_text=outgoing_text,
        resume_file_reference=snapshot.resume_file_reference,
    )


def _source_snapshot_dict(
    *, job: JobPosting, version: ResumeVersion, source_hash: str
) -> dict[str, Any]:
    """Build the ``ApplicationActionSourceSnapshot`` dict for the action.

    Carries only stable identifiers + the readiness source hash — never raw
    text, prompts, or secrets.
    """
    snap = ApplicationActionSourceSnapshot(
        job_id=job.id,
        resume_version_id=version.id,
        artifact_ids=[],
        source_hash=source_hash,
    )
    return snap.model_dump()


def _persist_platform_failure(
    db,
    *,
    run: AgentRun,
    record: ApplicationRecord,
    step_no: int,
    step_name: str,
    error: str,
    result: dict[str, Any],
    outcome: PrepareOutcome,
    diagnostic_reference: str | None = None,
) -> None:
    """Persist a failed step + failed run + failure envelope + timeline event.

    Maps a non-filled prepare outcome to its sanitized envelope code and
    next-action via :data:`PREPARE_FAILURE_CODES` (design.md §Failure Handling).
    """
    code, next_action_str = PREPARE_FAILURE_CODES[outcome]
    next_action = ApplicationFailureNextAction(next_action_str)

    envelope = build_failure_envelope(
        category=ApplicationFailureCategory.platform,
        code=code,
        message=error,
        retryable=next_action is ApplicationFailureNextAction.retry,
        next_action=next_action,
        agent_run_id=run.id,
        source_ids={
            "application_id": record.id,
            "run_id": run.id,
            "target_platform": "boss",
        },
    )

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=step_no,
        name=step_name,
        status="failed",
        error=error,
        result=result,
    )
    agent_run_repo.update_status(
        db,
        run,
        status="failed",
        finished_at=datetime.now(UTC),
        error=error,
    )

    event = application_repo.build_event(
        type="platform_failure",
        actor="system",
        to_status=record.status,
        summary=f"platform prepare failed: {code}",
        metadata={
            "agent_run_id": run.id,
            "error_code": code,
            "outcome": outcome.value,
            "diagnostic_reference": diagnostic_reference,
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_error=envelope.model_dump(mode="json"),
        latest_agent_run_id=run.id,
    )

    db.commit()


def _persist_filled_preview(
    db,
    *,
    run: AgentRun,
    record: ApplicationRecord,
    job: JobPosting,
    version: ResumeVersion,
    source_hash: str,
    snapshot: FilledSubmissionSnapshot,
) -> str:
    """Persist a successful filled preview: action + timeline + run status.

    Creates/updates a ``platform_submit`` :class:`ApplicationAction` whose
    ``payload_hash`` is computed from the exact filled snapshot, stores the
    external idempotency key, transitions the application to
    ``approval_required`` (when allowed), and marks the run ``succeeded``.

    Returns the created/updated action id.
    """
    preview = _action_preview_from_snapshot(snapshot)
    payload_hash = compute_payload_hash(preview)
    idempotency_key = compute_external_idempotency_key(
        application_id=record.id,
        action_type=ExternalActionType.platform_submit,
        payload_hash=payload_hash,
    )
    source_snapshot_dict = _source_snapshot_dict(
        job=job, version=version, source_hash=source_hash
    )

    # Reuse an existing non-terminal platform_submit action for this application
    # when one exists, so repeated prepares refresh the preview in place rather
    # than accumulating duplicate actions.
    existing = _find_active_platform_submit_action(db, record.id, record.user_id)
    if existing is not None:
        application_action_repo.update(
            db,
            existing,
            status=ExternalActionStatus.approval_required.value,
            payload_preview=preview.model_dump(mode="json"),
            payload_hash=payload_hash,
            source_snapshot=source_snapshot_dict,
            external_idempotency_key=idempotency_key,
            stale_reason=application_action_repo.CLEAR,
            approval=application_action_repo.CLEAR,
        )
        action = existing
    else:
        action = application_action_repo.create(
            db,
            application_id=record.id,
            user_id=record.user_id,
            action_type=ExternalActionType.platform_submit.value,
            status=ExternalActionStatus.approval_required.value,
            payload_preview=preview.model_dump(mode="json"),
            payload_hash=payload_hash,
            source_snapshot=source_snapshot_dict,
            external_idempotency_key=idempotency_key,
        )

    # Transition the application to approval_required (when allowed). The
    # timeline event records the platform preview and binds the action id +
    # payload hash — never raw form HTML, cookies, or tokens.
    to_status = ApplicationStatus.approval_required
    try:
        assert_transition(ApplicationStatus(record.status), to_status)
        event = application_repo.build_event(
            type="platform_preview_ready",
            actor="system",
            from_status=record.status,
            to_status=to_status.value,
            summary="platform submission preview ready for approval",
            metadata={
                "agent_run_id": run.id,
                "action_id": action.id,
                "payload_hash": payload_hash,
                "target_platform": snapshot.target_platform,
            },
        )
        application_repo.update_status_with_event(
            db,
            record,
            new_status=to_status.value,
            event=event,
            latest_agent_run_id=run.id,
        )
    except InvalidTransitionError:
        # Already in approval_required (or a lateral state): append the event
        # without changing status so the timeline still records the preview.
        event = application_repo.build_event(
            type="platform_preview_ready",
            actor="system",
            to_status=record.status,
            summary="platform submission preview refreshed for approval",
            metadata={
                "agent_run_id": run.id,
                "action_id": action.id,
                "payload_hash": payload_hash,
                "target_platform": snapshot.target_platform,
            },
        )
        application_repo.update_status_with_event(
            db,
            record,
            new_status=record.status,
            event=event,
            latest_agent_run_id=run.id,
        )

    # Finalize the run with sanitized metadata — only IDs, hashes, and counts.
    agent_run_repo.update_status(
        db,
        run,
        status="succeeded",
        finished_at=datetime.now(UTC),
        result={
            "application_id": record.id,
            "job_id": job.id,
            "resume_version_id": version.id,
            "action_id": action.id,
            "target_platform": snapshot.target_platform,
            "target_resource": snapshot.target_resource,
            "payload_hash": payload_hash,
            "external_idempotency_key": idempotency_key,
            "source_hash": source_hash,
            "field_count": len(snapshot.fields),
            "attachment_count": len(snapshot.attachments),
        },
    )

    db.commit()
    return action.id


def _find_active_platform_submit_action(db, application_id: str, user_id: str):
    """Return the most recent non-terminal ``platform_submit`` action, or None.

    "Non-terminal" means the action has not yet produced an external result
    (``external_result_status`` is NULL). Repeated prepares refresh this action
    in place instead of creating duplicates.
    """
    from sqlalchemy import select

    return (
        db.execute(
            select(ApplicationAction).where(
                (ApplicationAction.application_id == application_id)
                & (ApplicationAction.user_id == user_id)
                & (ApplicationAction.action_type == ExternalActionType.platform_submit.value)
                & (ApplicationAction.external_result_status.is_(None))
            )
            .order_by(ApplicationAction.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


async def run_platform_guided_submit_prepare_worker(
    *,
    application_id: str,
    job_id: str,
    resume_version_id: str,
    user_id: str,
    source_hash: str,
    agent_run_id: str,
    selected_artifact_ids: list[str],
    outgoing_text: str | None,
    resume_file_reference: str | None,
    target_resource: str,
) -> None:
    """Queue worker entry point — executes the prepare flow in the worker process.

    Opens its own DB session (never reuses a request-scoped ``Session``),
    re-loads + ownership-checks the application/job/resume version/profile,
    re-checks the source snapshot for staleness, flips the queued ``AgentRun``
    to ``running``, runs the BOSS adapter in dry-run/fill-only mode, and
    persists the result (filled preview or platform failure).

    Any unexpected error is caught by the handler wrapper
    (``queue.handlers.platform_guided_submit_prepare``), which calls
    :func:`fail_run` to persist a sanitized ``failed`` status.
    """
    from app.db.session import SessionLocal
    from app.platforms.boss.registry import get_adapter

    db = SessionLocal()
    try:
        run = agent_run_repo.get_run(db, agent_run_id)
        if run is None:
            _log.warning(
                "platform.worker.skip",
                application_id=application_id,
                agent_run_id=agent_run_id,
                reason="agent run not found",
            )
            return

        if run.status in {"succeeded", "failed"}:
            _log.info(
                "platform.worker.skip_terminal_run",
                application_id=application_id,
                agent_run_id=agent_run_id,
                status=run.status,
            )
            return

        # Re-check application ownership.
        record = application_repo.get_for_user(db, application_id, user_id)
        if record is None:
            _log.warning(
                "platform.worker.skip",
                application_id=application_id,
                agent_run_id=agent_run_id,
                reason="application not found or not owned",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="application not found or not owned",
            )
            db.commit()
            return

        # Re-check job ownership.
        job = db.get(JobPosting, job_id)
        if job is None or job.user_id != user_id:
            _log.warning(
                "platform.worker.skip",
                application_id=application_id,
                agent_run_id=agent_run_id,
                reason="job not found or not owned",
            )
            _persist_data_failure(
                db,
                run=run,
                record=record,
                error="job not found or not owned",
                code="job_context_missing",
                message="job context is unavailable",
                next_action=ApplicationFailureNextAction.edit_source,
            )
            return

        # Re-check resume version ownership.
        version = db.get(ResumeVersion, resume_version_id)
        if version is None:
            _log.warning(
                "platform.worker.skip",
                application_id=application_id,
                agent_run_id=agent_run_id,
                reason="resume version not found",
            )
            _persist_data_failure(
                db,
                run=run,
                record=record,
                error="resume version not found",
                code="resume_version_missing",
                message="resume version is unavailable",
                next_action=ApplicationFailureNextAction.choose_resume,
            )
            return

        profile = db.get(UserProfile, user_id)
        if profile is None:
            _persist_data_failure(
                db,
                run=run,
                record=record,
                error="user profile not found",
                code="profile_missing",
                message="user profile is unavailable",
                next_action=ApplicationFailureNextAction.manual_review,
            )
            return

        # Re-check the source snapshot for staleness. If the source changed
        # between enqueue and execution, the filled preview would be based on
        # stale data — fail the run before touching the platform.
        current_source_hash = compute_prepare_source_hash(
            job=job, version=version, profile=profile
        )
        if current_source_hash != source_hash:
            _log.warning(
                "platform.worker.stale_source",
                application_id=application_id,
                agent_run_id=agent_run_id,
                enqueue_hash=source_hash,
                current_hash=current_source_hash,
            )
            agent_run_repo.add_step(
                db,
                run_id=run.id,
                step_no=1,
                name=_STEP_LOAD_CONTEXT,
                status="failed",
                error="stale source detected",
                result={
                    "application_id": application_id,
                    "enqueue_source_hash": source_hash,
                    "current_source_hash": current_source_hash,
                },
            )
            envelope = build_failure_envelope(
                category=ApplicationFailureCategory.data,
                code="stale_source",
                message="source data changed since prepare was requested",
                retryable=False,
                next_action=ApplicationFailureNextAction.edit_source,
                agent_run_id=run.id,
                source_ids={
                    "application_id": application_id,
                    "run_id": run.id,
                },
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="stale source detected",
            )
            event = application_repo.build_event(
                type="platform_failure",
                actor="system",
                to_status=record.status,
                summary="platform prepare failed: stale_source",
                metadata={
                    "agent_run_id": run.id,
                    "error_code": "stale_source",
                },
            )
            application_repo.update_status_with_event(
                db,
                record,
                new_status=record.status,
                event=event,
                latest_error=envelope.model_dump(mode="json"),
                latest_agent_run_id=run.id,
            )
            db.commit()
            return

        # Flip the run to running.
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        agent_run_repo.update_status(db, run, status="running")
        db.commit()

        # Step 1 — load_application_context succeeded.
        agent_run_repo.add_step(
            db,
            run_id=run.id,
            step_no=1,
            name=_STEP_LOAD_CONTEXT,
            status="succeeded",
            result={
                "application_id": application_id,
                "job_id": job_id,
                "resume_version_id": resume_version_id,
                "source_hash": source_hash,
            },
        )

        # Step 2 — check_entry_guards succeeded (status + staleness already
        # verified above).
        agent_run_repo.add_step(
            db,
            run_id=run.id,
            step_no=2,
            name=_STEP_CHECK_ENTRY_GUARDS,
            status="succeeded",
            result={
                "application_status": record.status,
                "source_hash_matches": True,
            },
        )

        # Steps 3–7: invoke the adapter in dry-run/fill-only mode. The adapter
        # is selected via the registry (fake by default; real behind the env
        # flag). The adapter never performs the final submit action.
        adapter = get_adapter()
        ctx = PrepareContext(
            application_id=application_id,
            target_platform="boss",
            target_resource=target_resource,
            selected_artifact_ids=list(selected_artifact_ids),
            outgoing_text=outgoing_text,
            resume_file_reference=resume_file_reference,
            source_hash=source_hash,
            session_reference=None,
        )

        result: PrepareResult = await adapter.prepare_submission(ctx)

        # Record the adapter session/navigation/fill/classify/capture steps as a
        # single succeeded group. The fake adapter does not expose per-step
        # telemetry, so we record a coarse succeeded step for each named phase.
        for step_no, step_name in (
            (3, _STEP_OPEN_PLATFORM_SESSION),
            (4, _STEP_NAVIGATE),
            (5, _STEP_CLASSIFY_PAGE),
            (6, _STEP_FILL_FORM),
            (7, _STEP_CAPTURE_SNAPSHOT),
        ):
            agent_run_repo.add_step(
                db,
                run_id=run.id,
                step_no=step_no,
                name=step_name,
                status="succeeded",
                result={"outcome": result.outcome.value},
            )

        if result.outcome is PrepareOutcome.filled_preview and result.snapshot is not None:
            action_id = _persist_filled_preview(
                db,
                run=run,
                record=record,
                job=job,
                version=version,
                source_hash=source_hash,
                snapshot=result.snapshot,
            )
            # Step 8 — create_approval_action succeeded.
            agent_run_repo.add_step(
                db,
                run_id=run.id,
                step_no=8,
                name=_STEP_CREATE_APPROVAL_ACTION,
                status="succeeded",
                result={"action_id": action_id},
            )
            db.commit()
            _log.info(
                "platform.prepare_succeeded",
                application_id=application_id,
                agent_run_id=agent_run_id,
                action_id=action_id,
            )
            return

        # Non-filled outcome: persist the platform failure.
        _persist_platform_failure(
            db,
            run=run,
            record=record,
            step_no=8,
            step_name=_STEP_CREATE_APPROVAL_ACTION,
            error=result.message or f"platform prepare failed: {result.outcome.value}",
            result={
                "outcome": result.outcome.value,
                "failure_code": prepare_failure_code(result.outcome),
                "diagnostic_reference": result.diagnostic_reference,
            },
            outcome=result.outcome,
            diagnostic_reference=result.diagnostic_reference,
        )
        _log.warning(
            "platform.prepare_failed",
            application_id=application_id,
            agent_run_id=agent_run_id,
            outcome=result.outcome.value,
        )
    finally:
        db.close()


def _persist_data_failure(
    db,
    *,
    run: AgentRun,
    record: ApplicationRecord,
    error: str,
    code: str,
    message: str,
    next_action: ApplicationFailureNextAction,
) -> None:
    """Persist a data-category failure (missing job/resume/profile) + timeline."""
    envelope = build_failure_envelope(
        category=ApplicationFailureCategory.data,
        code=code,
        message=message,
        retryable=False,
        next_action=next_action,
        agent_run_id=run.id,
        source_ids={
            "application_id": record.id,
            "run_id": run.id,
        },
    )
    agent_run_repo.update_status(
        db,
        run,
        status="failed",
        finished_at=datetime.now(UTC),
        error=error,
    )
    event = application_repo.build_event(
        type="platform_failure",
        actor="system",
        to_status=record.status,
        summary=f"platform prepare failed: {code}",
        metadata={
            "agent_run_id": run.id,
            "error_code": code,
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_error=envelope.model_dump(mode="json"),
        latest_agent_run_id=run.id,
    )
    db.commit()


# ---------------------------------------------------------------------------
# Step 5 — Final submit behind approval + idempotency guards
# ---------------------------------------------------------------------------


class PlatformSubmitBlockedError(Exception):
    """Raised when a final submit is blocked by the approval/idempotency guard.

    ``reason`` is a stable machine code (mirrors
    :class:`ApprovalBlockedError.reason``); ``message`` is a safe user-facing
    string. The API layer maps this to HTTP 409. ``action_id`` lets the caller
    re-fetch the action after the guard refused execution.
    """

    def __init__(self, *, reason: str, message: str, action_id: str) -> None:
        self.reason = reason
        self.message = message
        self.action_id = action_id
        super().__init__(reason, message, action_id)


def load_submit_context(
    db,
    current_user: UserProfile,
    application_id: str,
    run_id: str,
) -> tuple[ApplicationRecord, AgentRun, ApplicationAction]:
    """Load + verify ownership + status for a final-submit request.

    Raises ``HTTPException`` (404/422/409) on ownership, status, or run-state
    failures. Returns ``(record, run, action)`` so the API endpoint can build a
    sanitized response without re-loading rows.

    The application status must be ``approved`` (design.md §State Machine
    Mapping); any other status raises 422. The referenced run must be the
    succeeded prepare run for this application and owned by the user.
    """
    from fastapi import HTTPException

    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="application not found")

    run = agent_run_repo.get_run(db, run_id)
    if run is None or run.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="run not found")

    # The run must be a prepare run tied to this application.
    if run.workflow_type != WORKFLOW_TYPE or (run.result or {}).get(
        "application_id"
    ) != application_id:
        raise HTTPException(status_code=404, detail="run not found")

    if run.status != "succeeded":
        raise HTTPException(
            status_code=409,
            detail=(
                f"prepare run must be succeeded before final submit (got {run.status})"
            ),
        )

    if record.status not in _SUBMITTABLE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                "application status must be approved, submitted, or failed to "
                f"submit a platform submission (got {record.status})"
            ),
        )

    action_id = (run.result or {}).get("action_id")
    if not action_id:
        raise HTTPException(
            status_code=409,
            detail="prepare run has no prepared action to submit",
        )

    action = application_action_repo.get_for_user_and_application(
        db,
        action_id=action_id,
        application_id=application_id,
        user_id=current_user.id,
    )
    if action is None:
        raise HTTPException(status_code=404, detail="prepared action not found")

    return record, run, action


async def run_platform_guided_submit_submit(
    db,
    *,
    current_user: UserProfile,
    application_id: str,
    run_id: str,
) -> tuple[ApplicationRecord, AgentRun, ApplicationAction]:
    """Execute the final platform submit behind the approval + idempotency guards.

    This runs synchronously inside the request (not a queue worker) because the
    adapter final submit is short-lived and the user is waiting for the result.

    The function performs, in order (design.md §API Shape → Submit endpoint
    requirements):

    1. Load + verify ownership + status (``load_submit_context``).
    2. Recompute the current payload hash from the stored filled snapshot.
    3. Recompute the current source hash from the current job/resume/profile.
    4. Call ``assert_action_approved`` (raises ``ApprovalBlockedError`` →
       mapped to 409) — this is the hard approval boundary.
    5. Check the external idempotency key: if a terminal result already exists
       for this key, return the existing action without touching the platform
       (design.md §H1).
    6. Call ``adapter.submit_prepared`` — the only external side effect.
    7. Persist the result:
       - ``submitted`` → action external_result_status=submitted, application
         → submitted, ``platform_submit_succeeded`` timeline event.
       - ``duplicate_detected`` → external_result_status=duplicate,
         ``platform_submit_unknown``/``platform_failure`` event per design.md.
       - ``unknown`` → external_result_status=unknown,
         ``platform_submit_unknown`` event + failure envelope.
       - ``platform_failure`` → external_result_status=failed,
         ``platform_failure`` event + failure envelope.

    Returns ``(record, run, action)`` after the result is persisted. Raises
    :class:`PlatformSubmitBlockedError` when the approval guard blocks
    execution (the caller maps it to 409).
    """
    record, run, action = load_submit_context(db, current_user, application_id, run_id)

    # Guard 0: external idempotency key. If a terminal result already exists for
    # this action, return the existing result without touching the platform and
    # without re-running the approval/source guards (design.md §H1: a repeated
    # submit request must be idempotent). This runs *before* the approval
    # boundary so a replay after the application has moved to ``submitted`` or
    # ``failed`` still returns the stored terminal result instead of a 422.
    idempotency_key = action.external_idempotency_key
    if idempotency_key is not None:
        terminal = application_action_repo.get_terminal_for_idempotency_key(
            db,
            external_idempotency_key=idempotency_key,
            user_id=current_user.id,
        )
        if terminal is not None:
            # Already submitted/duplicate/unknown/failed — return the existing
            # action. Append a timeline event noting the idempotent replay.
            _append_submit_blocked_timeline(
                db,
                record,
                run=run,
                action=action,
                reason="idempotency_replay",
                idempotency_key=idempotency_key,
            )
            db.commit()
            db.refresh(action)
            return record, run, action

    # Recompute the current payload hash from the stored filled snapshot so a
    # changed preview (e.g. a second prepare after approval) is detected. The
    # preview is the exact ``payload_preview`` on the action row.
    preview = ApplicationActionPreview.model_validate(action.payload_preview)
    current_payload_hash = compute_payload_hash(preview)

    # Recompute the current source hash from the current job/resume/profile.
    job = db.get(JobPosting, record.job_id)
    if job is None or job.user_id != current_user.id:
        raise PlatformSubmitBlockedError(
            reason="source_unavailable",
            message="job context is unavailable",
            action_id=action.id,
        )
    resume_version_id = record.resume_version_id
    if resume_version_id is None:
        raise PlatformSubmitBlockedError(
            reason="source_unavailable",
            message="resume version is missing",
            action_id=action.id,
        )
    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        raise PlatformSubmitBlockedError(
            reason="source_unavailable",
            message="resume version is unavailable",
            action_id=action.id,
        )
    profile = db.get(UserProfile, current_user.id)
    if profile is None:
        raise PlatformSubmitBlockedError(
            reason="source_unavailable",
            message="user profile is unavailable",
            action_id=action.id,
        )
    current_source_hash = compute_prepare_source_hash(
        job=job, version=version, profile=profile
    )

    # Step 9 — submit_final: approval + idempotency guards, then adapter call.
    # Guard 1: approval boundary. Raises ApprovalBlockedError on status/payload/
    # source mismatch. We catch it and re-raise as a 409-mapped blocked error.
    try:
        assert_action_approved(
            ApplicationActionOut.model_validate(action),
            current_payload_hash=current_payload_hash,
            current_source_hash=current_source_hash,
        )
    except ApprovalBlockedError as exc:
        raise PlatformSubmitBlockedError(
            reason=exc.reason,
            message=exc.message,
            action_id=action.id,
        ) from exc

    # Record the external-start timestamp so a concurrent submit is blocked by
    # ``get_running_for_idempotency_key`` (defensive — the request is
    # synchronous, but this makes the in-flight window auditable).
    started_at = datetime.now(UTC)
    application_action_repo.update(
        db,
        action,
        external_started_at=started_at,
    )
    db.flush()

    # Append a timeline event marking the submit start (design.md §Observability
    # → platform_submit_started).
    _append_submit_started_timeline(
        db,
        record,
        run=run,
        action=action,
        idempotency_key=idempotency_key,
    )

    # Invoke the adapter final submit. The adapter is selected via the registry
    # (fake by default; real behind the env flag). Only IDs + the filled
    # snapshot cross the boundary — never secrets or session data.
    from app.platforms.boss.registry import get_adapter

    adapter = get_adapter()
    snapshot = _snapshot_from_action(action)
    submit_ctx = SubmitContext(
        application_id=application_id,
        target_platform=preview.target_platform or "boss",
        target_resource=preview.target_resource or "",
        filled_snapshot=snapshot,
        session_reference=None,
    )
    result: SubmitResult = await adapter.submit_prepared(submit_ctx)

    # Step 10 — record_result: persist the sanitized terminal result.
    _persist_submit_result(
        db,
        record=record,
        run=run,
        action=action,
        result=result,
        started_at=started_at,
    )
    db.commit()
    db.refresh(action)
    return record, run, action


def _snapshot_from_action(action) -> FilledSubmissionSnapshot:
    """Reconstruct a ``FilledSubmissionSnapshot`` from the action preview.

    The action stores the payload preview (approval-boundary fields) but not the
    full snapshot. We synthesize a minimal snapshot carrying exactly the fields
    the adapter needs for the final submit: the target, the artifact IDs, the
    resume file reference, and the outgoing text as a single filled field. The
    adapter's ``submit_prepared`` only needs the snapshot to identify what to
    submit — it does not re-fill the form.
    """
    preview = ApplicationActionPreview.model_validate(action.payload_preview)
    fields: list = []
    if preview.outgoing_text is not None:
        from app.platforms.base import FilledField

        fields.append(
            FilledField(
                name="message",
                label="开场白",
                value=preview.outgoing_text,
                source_artifact_id=(
                    preview.selected_artifact_ids[0]
                    if preview.selected_artifact_ids
                    else None
                ),
            )
        )
    return FilledSubmissionSnapshot(
        target_platform=preview.target_platform or "boss",
        target_resource=preview.target_resource or "",
        application_id=action.application_id,
        selected_artifact_ids=list(preview.selected_artifact_ids),
        resume_file_reference=preview.resume_file_reference,
        fields=fields,
        attachments=[],
        page_state=_new_page_state(),
        captured_at=datetime.now(UTC),
    )


def _new_page_state():
    """Build a minimal ``FilledPageState`` for the synthesized submit snapshot."""
    from app.platforms.base import FilledPageState

    return FilledPageState(
        url_hash=None,
        title=None,
        final_submit_selector_seen=True,
    )


def _append_submit_started_timeline(
    db,
    record: ApplicationRecord,
    *,
    run: AgentRun,
    action,
    idempotency_key: str | None,
) -> None:
    """Append a ``platform_submit_started`` timeline event (no status change)."""
    event = application_repo.build_event(
        type="platform_submit_started",
        actor="system",
        to_status=record.status,
        summary="platform final submit started",
        metadata={
            "agent_run_id": run.id,
            "action_id": action.id,
            "external_idempotency_key": idempotency_key,
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_agent_run_id=run.id,
    )


def _append_submit_blocked_timeline(
    db,
    record: ApplicationRecord,
    *,
    run: AgentRun,
    action,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Append a ``platform_submit_blocked`` timeline event (no status change)."""
    event = application_repo.build_event(
        type="platform_submit_blocked",
        actor="system",
        to_status=record.status,
        summary=f"platform submit blocked: {reason}",
        metadata={
            "agent_run_id": run.id,
            "action_id": action.id,
            "reason": reason,
            "external_idempotency_key": idempotency_key,
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_agent_run_id=run.id,
    )


def _persist_submit_result(
    db,
    *,
    record: ApplicationRecord,
    run: AgentRun,
    action,
    result: SubmitResult,
    started_at: datetime,
) -> None:
    """Persist the terminal result of a final platform submit.

    Maps the adapter outcome to:

    - ``submitted`` → action external_result_status=submitted, application →
      submitted, ``platform_submit_succeeded`` timeline event, run succeeded.
    - ``duplicate_detected`` → action external_result_status=duplicate,
      ``platform_failure`` timeline event + failure envelope, run failed.
    - ``unknown`` → action external_result_status=unknown,
      ``platform_submit_unknown`` timeline event + failure envelope, run failed.
    - ``platform_failure`` → action external_result_status=failed,
      ``platform_failure`` timeline event + failure envelope, run failed.

    All persisted metadata is sanitized: only IDs, hashes, outcome, and the
    failure code. No cookies, tokens, credentials, raw JD, raw resume, or page
    HTML.
    """
    completed_at = datetime.now(UTC)
    outcome = result.outcome
    now = completed_at

    if outcome is SubmitOutcome.submitted:
        # Confirmed success: persist the terminal result, transition the
        # application to submitted, append the success timeline event.
        external_result = ExternalActionResult(
            result_status=ExternalActionResultStatus.submitted,
            started_at=started_at,
            completed_at=completed_at,
            result={
                "platform_reference": result.platform_reference,
                "target_platform": action.payload_preview.get("target_platform"),
            },
        )
        application_action_repo.update(
            db,
            action,
            external_completed_at=completed_at,
            external_result_status=ExternalActionResultStatus.submitted.value,
            external_result=external_result.model_dump(mode="json"),
        )
        to_status = ApplicationStatus.submitted
        try:
            assert_transition(ApplicationStatus(record.status), to_status)
            event = application_repo.build_event(
                type="platform_submit_succeeded",
                actor="system",
                from_status=record.status,
                to_status=to_status.value,
                summary="platform submission submitted",
                metadata={
                    "agent_run_id": run.id,
                    "action_id": action.id,
                    "platform_reference": result.platform_reference,
                },
            )
            application_repo.update_status_with_event(
                db,
                record,
                new_status=to_status.value,
                event=event,
                latest_agent_run_id=run.id,
            )
        except InvalidTransitionError:
            # Defensive: if the transition is not allowed (unexpected state),
            # append the event without changing status so the timeline still
            # records the confirmed submit.
            event = application_repo.build_event(
                type="platform_submit_succeeded",
                actor="system",
                to_status=record.status,
                summary="platform submission submitted",
                metadata={
                    "agent_run_id": run.id,
                    "action_id": action.id,
                    "platform_reference": result.platform_reference,
                },
            )
            application_repo.update_status_with_event(
                db,
                record,
                new_status=record.status,
                event=event,
                latest_agent_run_id=run.id,
            )
        agent_run_repo.update_status(
            db,
            run,
            status="succeeded",
            finished_at=now,
            result={
                **(run.result or {}),
                "submit_outcome": outcome.value,
                "platform_reference": result.platform_reference,
            },
        )
        return

    # Non-submitted outcome: map to the failure envelope code + next action.
    code, next_action_str = SUBMIT_FAILURE_CODES[outcome]
    next_action = ApplicationFailureNextAction(next_action_str)

    result_status = {
        SubmitOutcome.duplicate_detected: ExternalActionResultStatus.duplicate,
        SubmitOutcome.unknown: ExternalActionResultStatus.unknown,
        SubmitOutcome.platform_failure: ExternalActionResultStatus.failed,
    }[outcome]

    external_result = ExternalActionResult(
        result_status=result_status,
        started_at=started_at,
        completed_at=completed_at,
        result={
            "failure_code": code,
            "outcome": outcome.value,
            "message": result.message,
        },
    )
    application_action_repo.update(
        db,
        action,
        external_completed_at=completed_at,
        external_result_status=result_status.value,
        external_result=external_result.model_dump(mode="json"),
    )

    envelope = build_failure_envelope(
        category=ApplicationFailureCategory.platform,
        code=code,
        message=result.message or f"platform submit failed: {outcome.value}",
        retryable=next_action is ApplicationFailureNextAction.retry,
        next_action=next_action,
        agent_run_id=run.id,
        source_ids={
            "application_id": record.id,
            "run_id": run.id,
            "action_id": action.id,
            "target_platform": action.payload_preview.get("target_platform"),
        },
    )

    agent_run_repo.update_status(
        db,
        run,
        status="failed",
        finished_at=now,
        error=result.message or f"platform submit failed: {outcome.value}",
    )

    event_type = (
        "platform_submit_unknown"
        if outcome is SubmitOutcome.unknown
        else "platform_failure"
    )
    event = application_repo.build_event(
        type=event_type,
        actor="system",
        to_status=record.status,
        summary=f"platform submit failed: {code}",
        metadata={
            "agent_run_id": run.id,
            "action_id": action.id,
            "error_code": code,
            "outcome": outcome.value,
            "diagnostic_reference": result.diagnostic_reference,
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_error=envelope.model_dump(mode="json"),
        latest_agent_run_id=run.id,
    )


def abort_platform_submit(
    db,
    *,
    current_user: UserProfile,
    application_id: str,
    run_id: str,
) -> tuple[ApplicationRecord, AgentRun, ApplicationAction]:
    """Abort an in-flight platform final submit.

    Aborting is a safe, side-effect-free cancellation. It:

    - verifies ownership + that the run is the succeeded prepare run for this
      application (404/409 on mismatch);
    - revokes the prepared ``ApplicationAction`` so it can no longer authorize
      execution (``assert_action_approved`` will block any later submit);
    - appends a ``platform_submit_blocked`` timeline event noting the abort.

    No platform side effect is performed. If the adapter already produced a
    terminal result, the abort is refused with 409 (the result stands and must
    be reconciled manually).
    """
    from fastapi import HTTPException

    record, run, action = load_submit_context(db, current_user, application_id, run_id)

    # If the action already has a terminal result, the submit completed (or
    # failed) — aborting is no longer meaningful.
    if action.external_result_status is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "cannot abort a platform submission that already has a terminal "
                f"result ({action.external_result_status})"
            ),
        )

    # If the external submit is in-flight (external_started_at set, no terminal
    # result), we cannot safely cancel the browser action — refuse with 409 so
    # the user knows the result must be reconciled manually.
    if action.external_started_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "cannot abort a platform submission while the adapter final "
                "submit is in flight; reconcile the result manually"
            ),
        )

    # Revoke the action so it can no longer authorize execution.
    application_action_repo.update(
        db,
        action,
        status=ExternalActionStatus.revoked.value,
        approval=application_action_repo.CLEAR,
        stale_reason=application_action_repo.CLEAR,
    )

    event = application_repo.build_event(
        type="platform_submit_blocked",
        actor="user",
        to_status=record.status,
        summary="platform submit aborted by user",
        metadata={
            "agent_run_id": run.id,
            "action_id": action.id,
            "reason": "user_aborted",
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_agent_run_id=run.id,
    )
    db.commit()
    db.refresh(action)
    return record, run, action


__all__ = [
    "PlatformSubmitBlockedError",
    "WORKFLOW_TYPE",
    "abort_platform_submit",
    "compute_prepare_source_hash",
    "load_prepare_context",
    "load_submit_context",
    "run_platform_guided_submit_prepare_worker",
    "run_platform_guided_submit_submit",
]
