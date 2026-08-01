"""Readiness artifact generation service — context loader + workflow orchestration.

This module owns the readiness artifact generation workflow:

- :func:`load_readiness_context`: read and verify ownership of the current
  user's profile, the application record, the target job, and the selected
  resume version, then build a ``ReadinessContext`` ready for the prompt
  builder and executor.
- :func:`run_readiness_generation_worker`: the queue worker entry point. Opens
  its own DB session, re-loads + ownership-checks all sources, re-checks the
  source snapshot for staleness, flips the queued ``AgentRun`` to ``running``,
  and delegates to :func:`_execute_readiness_generation` for the fixed-step
  orchestration (model call → validation → sanitized step/run persistence →
  ``GeneratedArtifact`` + application timeline event).

Ownership rules (design.md §error table):

- application missing or not owned by the current user → ``404 application not
  found``;
- job missing or not owned by the current user → ``404 application not found``;
- resume version missing, or its parent resume not owned by the current user
  → ``404 application not found``;
- resume version ``raw_text`` empty/blank → ``422 resume version has no
  parsed text``.

Cross-user access returns 404 (not 403) to avoid revealing resource existence,
matching the convention used in ``app/api/v1/jobs.py`` and ``resumes.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.agents.prompts.readiness import (
    PROMPT_VERSION,
    ReadinessContext,
)
from app.agents.readiness_executor import (
    ReadinessExecution,
    ReadinessExecutor,
    ReadinessValidationError,
    failed_execution,
    serialize_output,
    usage_to_dict,
)
from app.core.logging import get_logger
from app.db.models.models import (
    ApplicationRecord,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.repositories import (
    agent_run_repo,
    application_repo,
    generated_artifact_repo,
    resume_repo,
)
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway
from app.services.application_state import (
    ApplicationFailureCategory,
    ApplicationFailureNextAction,
    build_failure_envelope,
    build_source_snapshot,
)

_log = get_logger("app.services.readiness_service")

#: The fixed workflow type stored on ``AgentRun.workflow_type``.
WORKFLOW_TYPE = "readiness_generation"

#: Fixed step names produced by the workflow (design.md read APIs / R3).
_STEP_LOAD_CONTEXT = "load_context"
_STEP_BUILD_PROMPT_CONTEXT = "build_prompt_context"
_STEP_CALL_MODEL = "call_model"
_STEP_VALIDATE_MODEL_OUTPUT = "validate_model_output"
_STEP_PERSIST_OUTPUTS = "persist_outputs"
_STEP_COMPLETE_RUN = "complete_run"


def _job_to_dict(job: JobPosting) -> dict[str, Any]:
    """Project a ``JobPosting`` into the compact job dict used by the prompt."""
    return {
        "id": job.id,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "salary_range": job.salary_range,
        "direction": job.direction,
        "jd_raw": job.jd_raw,
    }


def _profile_to_dict(profile: UserProfile) -> dict[str, Any]:
    """Project a ``UserProfile`` into the compact profile dict for the prompt.

    Named constraint fields are flattened to top-level keys so the prompt
    renders them as clear labels rather than an opaque ``constraints`` blob.
    Legacy unknown keys inside ``constraints`` are not surfaced to the model
    (they are read-only back-compat data for the UI).
    """
    raw_constraints = profile.constraints or {}
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "email": profile.email,
        "career_direction": profile.career_direction,
        "base_location": profile.base_location,
        "preferred_locations": profile.preferred_locations,
        "salary_min": profile.salary_min,
        "salary_max": profile.salary_max,
        "strengths": profile.strengths,
        "deal_breakers": raw_constraints.get("deal_breakers"),
        "preferred_company_types": raw_constraints.get("preferred_company_types"),
        "preferred_industries": raw_constraints.get("preferred_industries"),
        "work_mode_preference": raw_constraints.get("work_mode_preference"),
        "commute_preference": raw_constraints.get("commute_preference"),
        "career_goals": raw_constraints.get("career_goals"),
        "resume_tailoring_notes": raw_constraints.get("resume_tailoring_notes"),
        "availability_notes": raw_constraints.get("availability_notes"),
    }


def _resume_to_dict(resume: Resume, version: ResumeVersion) -> dict[str, Any]:
    """Project a resume + version into the resume dict for the prompt.

    The typed ``facts`` key exposes the structured resume facts (when extraction
    has run) as a clean object, separate from the parser telemetry
    (``_parser``/``_parser_status``/``_extraction``) that lives inside
    ``parsed_facts``. When no facts have been extracted yet, ``facts`` is an
    empty dict so the prompt degrades gracefully to reasoning over ``raw_text``
    only.
    """
    facts = version.parsed_facts or {}
    return {
        "resume_id": resume.id,
        "resume_version_id": version.id,
        "filename": resume.filename,
        "parser_status": facts.get("_parser_status"),
        "parser_name": facts.get("_parser"),
        "raw_text": version.raw_text or "",
        "parsed_facts": facts,
        "facts": facts.get("facts") or {},
    }


def _compute_source_hash(
    *,
    job: JobPosting,
    version: ResumeVersion,
    profile: UserProfile,
) -> str:
    """Compute the current source hash for the readiness sources.

    The hash covers job/resume/profile metadata — never raw text. When any of
    these change, the hash changes and previously generated artifacts are
    considered stale.
    """
    snapshot = build_source_snapshot(
        job_id=job.id,
        job_updated_at=job.updated_at,
        resume_version_id=version.id,
        resume_version_no=version.version_no,
        profile_updated_at=profile.updated_at,
        prompt_versions={"readiness": PROMPT_VERSION},
    )
    return snapshot.source_hash


def load_readiness_context(
    db,
    current_user: UserProfile,
    application_id: str,
    artifact_type: str,
) -> tuple[ReadinessContext, ApplicationRecord, JobPosting, ResumeVersion, Resume]:
    """Load and verify all sources, returning a ready ``ReadinessContext``.

    Raises ``HTTPException`` (404/422) on ownership or data-quality failures.
    Does not call the model and does not persist anything.

    Returns ``(context, application, job, version, resume)`` so the caller
    (API endpoint) can use the ORM objects for sanitized metadata without
    re-loading them.
    """
    from fastapi import HTTPException

    # 1. Verify application ownership.
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "readiness.application_not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")

    # 2. Verify job ownership via the application's job_id.
    job = db.get(JobPosting, record.job_id)
    if job is None or job.user_id != current_user.id:
        _log.info(
            "readiness.application_not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")

    # 3. Verify resume version ownership.
    resume_version_id = record.resume_version_id
    if resume_version_id is None:
        raise HTTPException(
            status_code=422,
            detail="application has no resume version bound",
        )

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="application not found")

    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="application not found")

    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        _log.info(
            "readiness.resume_version_no_text",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=422, detail="resume version has no parsed text")

    source_hash = _compute_source_hash(job=job, version=version, profile=current_user)

    context = ReadinessContext(
        user_id=current_user.id,
        artifact_type=artifact_type,
        profile=_profile_to_dict(current_user),
        job=_job_to_dict(job),
        resume=_resume_to_dict(resume, version),
        source_hash=source_hash,
    )
    return context, record, job, version, resume


def _build_source_ids(
    *,
    user_id: str,
    application_id: str,
    job_id: str,
    resume_id: str,
    resume_version_id: str,
    artifact_type: str,
    source_hash: str,
    execution: ReadinessExecution,
) -> dict[str, Any]:
    """Assemble the ``GeneratedArtifact.source_ids`` provenance dict.

    Per design §Freshness the artifact carries the ``source_hash`` so the UI
    can later detect stale artifacts. Raw resume text or full prompts are
    deliberately NOT included.
    """
    source_ids: dict[str, Any] = {
        "user_id": user_id,
        "application_id": application_id,
        "job_id": job_id,
        "resume_id": resume_id,
        "resume_version_id": resume_version_id,
        "artifact_type": artifact_type,
        "source_hash": source_hash,
        "model_request_id": execution.request_id,
        "provider": execution.provider,
        "model": execution.model,
        "prompt_version": PROMPT_VERSION,
        "workflow_type": WORKFLOW_TYPE,
    }
    if execution.usage:
        source_ids["usage"] = execution.usage
    return source_ids


def _persist_failure(
    db,
    *,
    run: AgentRun,
    application_id: str,
    step_no: int,
    step_name: str,
    error: str,
    result: dict[str, Any],
    category: ApplicationFailureCategory,
    code: str,
    message: str,
    retryable: bool,
    next_action: ApplicationFailureNextAction,
) -> None:
    """Persist a failed step + failed run + failure envelope + timeline event.

    This is the shared failure path for the readiness worker (design.md
    §Failure Persistence). It updates:
    - AgentStep (failed, with sanitized error/result)
    - AgentRun (failed, with sanitized error)
    - ApplicationRecord latest_error (failure envelope) + latest_agent_run_id
    - Application timeline event
    """
    envelope = build_failure_envelope(
        category=category,
        code=code,
        message=message,
        retryable=retryable,
        next_action=next_action,
        agent_run_id=run.id,
        source_ids={
            "application_id": application_id,
            "run_id": run.id,
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

    record = application_repo.get_for_user(db, application_id, run.user_id or "")
    if record is not None:
        event = application_repo.build_event(
            type="artifact_generation_failed",
            actor="system",
            to_status=record.status,
            summary=f"artifact generation failed: {code}",
            metadata={
                "artifact_type": result.get("artifact_type"),
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


def persist_application_failure(
    db,
    *,
    run: AgentRun,
    application_id: str,
    artifact_type: str,
    error: str,
    category: ApplicationFailureCategory,
    code: str,
    message: str,
    retryable: bool,
    next_action: ApplicationFailureNextAction,
    status: str = "failed",
) -> None:
    """Persist a failed run plus application-visible failure state.

    Used for failures that happen before the fixed executor steps can run
    (queue enqueue failure, missing worker context, unusable resume). The stored
    envelope and timeline metadata are sanitized and ID-only so the frontend can
    show/retry the failure without leaking raw JD or resume text.
    """
    envelope = build_failure_envelope(
        category=category,
        code=code,
        message=message,
        retryable=retryable,
        next_action=next_action,
        agent_run_id=run.id,
        source_ids={
            "application_id": application_id,
            "artifact_type": artifact_type,
            "run_id": run.id,
        },
    )
    agent_run_repo.update_status(
        db,
        run,
        status=status,
        finished_at=datetime.now(UTC),
        error=error,
    )

    record = application_repo.get_for_user(db, application_id, run.user_id or "")
    if record is not None:
        event = application_repo.build_event(
            type="artifact_generation_failed",
            actor="system",
            to_status=record.status,
            summary=f"artifact generation failed: {code}",
            metadata={
                "artifact_type": artifact_type,
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


async def _execute_readiness_generation(
    db,
    run: AgentRun,
    context: ReadinessContext,
    application_id: str,
    gateway: ModelGateway,
) -> ReadinessExecution:
    """Drive the fixed-step readiness generation orchestration using an existing run.

    Owns steps 1–6:

    1. ``load_context`` — record the already-verified context as a succeeded step.
    2. ``build_prompt_context`` — assemble the chat messages + truncation metadata.
    3. ``call_model`` — send the chat request via the gateway. A gateway/provider
       error fails the run with a sanitized step and persists the failure
       envelope + timeline event.
    4. ``validate_model_output`` — parse + schema-validate the response. On
       :class:`ReadinessValidationError` a failed step + failed run are persisted
       (sanitized) and no ``GeneratedArtifact`` is created.
    5. ``persist_outputs`` — create ``GeneratedArtifact`` + append timeline event.
    6. ``complete_run`` — finalize run status and metadata.

    Failures return a placeholder :class:`ReadinessExecution` instead of
    raising; the run is already marked ``failed`` on disk so the worker simply
    stops.

    All persisted ``AgentStep``/``AgentRun`` results are sanitized — no raw JD or
    resume text crosses into the stored audit trail (design Phase 4 checklist).
    """
    artifact_type = context.artifact_type
    executor = ReadinessExecutor(gateway)

    def _fail(
        step_no: int,
        step_name: str,
        *,
        error: str,
        result: dict[str, Any],
        category: ApplicationFailureCategory,
        code: str,
        message: str,
        retryable: bool,
        next_action: ApplicationFailureNextAction,
    ) -> None:
        _persist_failure(
            db,
            run=run,
            application_id=application_id,
            step_no=step_no,
            step_name=step_name,
            error=error,
            result=result,
            category=category,
            code=code,
            message=message,
            retryable=retryable,
            next_action=next_action,
        )

    # Step 1 — load_context succeeded.
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=1,
        name=_STEP_LOAD_CONTEXT,
        status="succeeded",
        result={
            "application_id": application_id,
            "job_id": context.job.get("id"),
            "resume_version_id": context.resume.get("resume_version_id"),
            "resume_id": context.resume.get("resume_id"),
            "artifact_type": artifact_type,
            "source_hash": context.source_hash,
        },
    )

    # Step 2 — build_prompt_context.
    prompt = executor.build_prompt(context)
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=2,
        name=_STEP_BUILD_PROMPT_CONTEXT,
        status="succeeded",
        result={
            "prompt_version": PROMPT_VERSION,
            "message_count": len(prompt.messages),
            "truncation": prompt.truncation,
        },
    )

    # Step 3 — call_model.
    try:
        response = await executor.call_model(prompt.messages)
    except Exception as exc:
        _log.warning(
            "readiness.model_call_failed",
            run_id=run.id,
            artifact_type=artifact_type,
            provider=gateway.provider_name,
            error=str(exc),
        )
        _fail(
            3,
            _STEP_CALL_MODEL,
            error="model call failed",
            result={
                "artifact_type": artifact_type,
                "provider": gateway.provider_name,
                "error_type": type(exc).__name__,
            },
            category=ApplicationFailureCategory.model,
            code="model_call_failed",
            message="model call failed",
            retryable=True,
            next_action=ApplicationFailureNextAction.retry,
        )
        return failed_execution()

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=3,
        name=_STEP_CALL_MODEL,
        status="succeeded",
        result={
            "artifact_type": artifact_type,
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
            "latency_ms": response.latency_ms,
        },
    )

    # Step 4 — validate_model_output.
    try:
        output = executor.validate(response, artifact_type)
    except ReadinessValidationError as exc:
        _log.warning(
            "readiness.model_invalid",
            run_id=run.id,
            artifact_type=artifact_type,
            kind=exc.kind,
            request_id=exc.request_id,
            provider=response.provider,
        )
        _fail(
            4,
            _STEP_VALIDATE_MODEL_OUTPUT,
            error="model returned invalid output",
            result={
                "artifact_type": artifact_type,
                "kind": exc.kind,
                "request_id": exc.request_id,
            },
            category=ApplicationFailureCategory.validation,
            code="invalid_model_output",
            message="model returned invalid output",
            retryable=True,
            next_action=ApplicationFailureNextAction.retry,
        )
        return failed_execution()

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=4,
        name=_STEP_VALIDATE_MODEL_OUTPUT,
        status="succeeded",
        result={
            "artifact_type": artifact_type,
            "kind": "schema_valid",
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
        },
    )

    execution = ReadinessExecution(
        output=output,
        truncation=prompt.truncation,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        usage=usage_to_dict(response.usage),
    )

    # Step 5 — persist_outputs. The validated output is the persistence gate:
    # only after this block does a GeneratedArtifact exist.
    job_id = str(context.job.get("id") or "")
    resume_id = str(context.resume.get("resume_id") or "")
    resume_version_id = str(context.resume.get("resume_version_id") or "")

    source_ids = _build_source_ids(
        user_id=context.user_id,
        application_id=application_id,
        job_id=job_id,
        resume_id=resume_id,
        resume_version_id=resume_version_id,
        artifact_type=artifact_type,
        source_hash=context.source_hash,
        execution=execution,
    )

    artifact = generated_artifact_repo.create(
        db,
        artifact_type=artifact_type,
        content=serialize_output(output),
        user_id=context.user_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        agent_run_id=run.id,
        source_ids=source_ids,
        prompt_version=PROMPT_VERSION,
        model_name=execution.model,
    )

    # Append a timeline event to the application record.
    record = application_repo.get_for_user(db, application_id, context.user_id)
    if record is not None:
        event = application_repo.build_event(
            type="artifact_generated",
            actor="system",
            to_status=record.status,
            summary=f"artifact generated: {artifact_type}",
            metadata={
                "artifact_type": artifact_type,
                "artifact_id": artifact.id,
                "agent_run_id": run.id,
                "source_hash": context.source_hash,
            },
        )
        application_repo.update_status_with_event(
            db,
            record,
            new_status=record.status,
            event=event,
            latest_agent_run_id=run.id,
            readiness_snapshot={
                "source_hash": context.source_hash,
                "artifact_type": artifact_type,
                "artifact_id": artifact.id,
            },
        )

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=5,
        name=_STEP_PERSIST_OUTPUTS,
        status="succeeded",
        result={
            "artifact_type": artifact_type,
            "artifact_id": artifact.id,
        },
    )

    # Step 6 — complete_run.
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=6,
        name=_STEP_COMPLETE_RUN,
        status="succeeded",
        result={"status": "succeeded"},
    )

    agent_run_repo.update_status(
        db,
        run,
        status="succeeded",
        finished_at=datetime.now(UTC),
        result={
            "application_id": application_id,
            "job_id": job_id,
            "resume_version_id": resume_version_id,
            "resume_id": resume_id,
            "artifact_type": artifact_type,
            "artifact_id": artifact.id,
            "provider": execution.provider,
            "model": execution.model,
            "model_request_id": execution.request_id,
            "prompt_version": PROMPT_VERSION,
            "source_hash": context.source_hash,
            "source_context": {
                "truncation": execution.truncation,
            },
        },
    )

    db.commit()
    return execution


async def run_readiness_generation_worker(
    application_id: str,
    job_id: str,
    resume_version_id: str,
    user_id: str,
    artifact_type: str,
    source_hash: str,
    agent_run_id: str,
    gateway: ModelGateway,
) -> None:
    """Queue worker entry point — executes readiness generation in the worker process.

    Opens its own DB session (never reuses a request-scoped ``Session``),
    re-loads + ownership-checks the application/job/resume/version/profile,
    re-checks the source snapshot for staleness (design.md §Freshness: if the
    source hash changed between enqueue and execution, the run is failed
    without persisting an artifact), re-loads the queued ``AgentRun`` by ID,
    flips it to ``running``, and delegates to
    :func:`_execute_readiness_generation`.

    Any unexpected error is caught by the handler wrapper
    (``queue.handlers.readiness_generation``), which calls :func:`fail_run` to
    persist a sanitized ``failed`` status so the run never stays stuck on
    ``running``.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        run = agent_run_repo.get_run(db, agent_run_id)
        if run is None:
            _log.warning(
                "readiness.worker.skip",
                application_id=application_id,
                agent_run_id=agent_run_id,
                reason="agent run not found",
            )
            return

        if run.status in {"succeeded", "failed"}:
            _log.info(
                "readiness.worker.skip_terminal_run",
                application_id=application_id,
                agent_run_id=agent_run_id,
                status=run.status,
            )
            return

        # Re-check application ownership.
        record = application_repo.get_for_user(db, application_id, user_id)
        if record is None:
            _log.warning(
                "readiness.worker.skip",
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
                "readiness.worker.skip",
                application_id=application_id,
                agent_run_id=agent_run_id,
                reason="job not found or not owned",
            )
            persist_application_failure(
                db,
                run=run,
                application_id=application_id,
                artifact_type=artifact_type,
                error="job not found or not owned",
                category=ApplicationFailureCategory.data,
                code="job_context_missing",
                message="job context is unavailable",
                retryable=False,
                next_action=ApplicationFailureNextAction.edit_source,
            )
            db.commit()
            return

        # Re-check resume version ownership.
        version = db.get(ResumeVersion, resume_version_id)
        if version is None:
            _log.warning(
                "readiness.worker.skip",
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="resume version not found",
            )
            persist_application_failure(
                db,
                run=run,
                application_id=application_id,
                artifact_type=artifact_type,
                error="resume version not found",
                category=ApplicationFailureCategory.data,
                code="resume_version_missing",
                message="resume version is unavailable",
                retryable=False,
                next_action=ApplicationFailureNextAction.choose_resume,
            )
            db.commit()
            return

        resume = resume_repo.get(db, version.resume_id)
        if resume is None or resume.user_id != user_id:
            _log.warning(
                "readiness.worker.skip",
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="resume not found or not owned",
            )
            persist_application_failure(
                db,
                run=run,
                application_id=application_id,
                artifact_type=artifact_type,
                error="resume version not found",
                category=ApplicationFailureCategory.data,
                code="resume_version_missing",
                message="resume version is unavailable",
                retryable=False,
                next_action=ApplicationFailureNextAction.choose_resume,
            )
            db.commit()
            return

        raw_text = (version.raw_text or "").strip()
        if not raw_text:
            _log.warning(
                "readiness.worker.skip",
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="no parsed text",
            )
            persist_application_failure(
                db,
                run=run,
                application_id=application_id,
                artifact_type=artifact_type,
                error="resume version has no parsed text",
                category=ApplicationFailureCategory.data,
                code="resume_text_missing",
                message="resume version has no parsed text",
                retryable=False,
                next_action=ApplicationFailureNextAction.choose_resume,
            )
            db.commit()
            return

        # Re-load the user profile.
        profile = db.get(UserProfile, user_id)
        if profile is None:
            _log.warning(
                "readiness.worker.skip",
                agent_run_id=agent_run_id,
                reason="user profile not found",
            )
            persist_application_failure(
                db,
                run=run,
                application_id=application_id,
                artifact_type=artifact_type,
                error="user profile not found",
                category=ApplicationFailureCategory.data,
                code="profile_missing",
                message="user profile is unavailable",
                retryable=False,
                next_action=ApplicationFailureNextAction.manual_review,
            )
            db.commit()
            return

        # Recompute the source hash and check for staleness. If the source
        # changed between enqueue and execution, the artifact would be based on
        # stale data — fail the run without persisting an artifact (design.md
        # §Freshness: "Stale source after run start should prevent current
        # marking").
        current_source_hash = _compute_source_hash(
            job=job, version=version, profile=profile
        )
        if current_source_hash != source_hash:
            _log.warning(
                "readiness.worker.stale_source",
                application_id=application_id,
                agent_run_id=agent_run_id,
                artifact_type=artifact_type,
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
                    "artifact_type": artifact_type,
                    "enqueue_source_hash": source_hash,
                    "current_source_hash": current_source_hash,
                },
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="stale source detected",
            )

            # Persist the failure envelope + timeline event.
            envelope = build_failure_envelope(
                category=ApplicationFailureCategory.data,
                code="stale_source",
                message="source data changed since generation was requested",
                retryable=True,
                next_action=ApplicationFailureNextAction.retry,
                agent_run_id=run.id,
                source_ids={
                    "application_id": application_id,
                    "artifact_type": artifact_type,
                },
            )
            event = application_repo.build_event(
                type="artifact_generation_failed",
                actor="system",
                to_status=record.status,
                summary="artifact generation failed: stale_source",
                metadata={
                    "artifact_type": artifact_type,
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

        # Flip queued → running on entry.
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        agent_run_repo.update_status(db, run, status="running")
        db.commit()

        context = ReadinessContext(
            user_id=user_id,
            artifact_type=artifact_type,
            profile=_profile_to_dict(profile),
            job=_job_to_dict(job),
            resume=_resume_to_dict(resume, version),
            source_hash=current_source_hash,
        )

        try:
            await _execute_readiness_generation(
                db=db,
                run=run,
                context=context,
                application_id=application_id,
                gateway=gateway,
            )
        except Exception as exc:  # noqa: BLE001 — never let the worker crash unseen
            _log.warning(
                "readiness.worker.error",
                application_id=application_id,
                agent_run_id=agent_run_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            db.rollback()
            fresh_run = agent_run_repo.get_run(db, agent_run_id)
            if fresh_run is not None and fresh_run.status not in {"succeeded", "failed"}:
                agent_run_repo.update_status(
                    db,
                    fresh_run,
                    status="failed",
                    finished_at=datetime.now(UTC),
                    error="readiness generation failed",
                )
                db.commit()
    finally:
        db.close()
