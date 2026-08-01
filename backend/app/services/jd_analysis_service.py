"""JD analysis service — context loader + workflow orchestration.

This module owns the resume-aware JD analysis workflow:

- :func:`load_jd_analysis_context` (Phase 2): read and verify ownership of the
  current user's profile, the target job, and the selected resume version, then
  build a ``JdAnalysisContext`` ready for the prompt builder and executor.
- :func:`run_resume_aware_jd_analysis` (Phase 4): the deterministic
  orchestration transaction. It creates an ``AgentRun``, drives the fixed
  steps (``load_context`` → ``analyze_with_model`` → ``persist_outputs``),
  persists ``AgentStep`` / ``JobAnalysis`` / ``GeneratedArtifact`` rows, and
  translates failures into the API error contract (design.md §5.1, §9).

Ownership rules (design.md §5.1 error table):

- job missing or not owned by the current user → ``404 job not found``;
- resume version missing, or its parent resume not owned by the current user
  → ``404 resume version not found``;
- resume version ``raw_text`` empty/blank → ``422 resume version has no
  parsed text``.

Cross-user access returns 404 (not 403) to avoid revealing resource existence,
matching the convention used in ``app/api/v1/jobs.py`` and ``resumes.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.jd_analysis_executor import (
    JdAnalysisExecution,
    JdAnalysisExecutor,
    JdAnalysisValidationError,
    _usage_to_dict,
)
from app.agents.prompts.jd_analysis import (
    PROMPT_VERSION,
    JdAnalysisContext,
)
from app.core.logging import get_logger
from app.db.models.models import (
    GeneratedArtifact,
    JobAnalysis,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.repositories import (
    agent_run_repo,
    generated_artifact_repo,
    job_analysis_repo,
    resume_repo,
)
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway

_log = get_logger("app.services.jd_analysis_service")

#: The fixed workflow type stored on ``AgentRun.workflow_type``.
WORKFLOW_TYPE = "resume_aware_jd_analysis"

#: Fixed step names produced by the workflow (design.md read APIs / R3). The
#: order matches the step_no persisted on each ``AgentStep``. Splitting the old
#: coarse ``analyze_with_model`` step into ``build_prompt_context`` /
#: ``call_model`` / ``validate_model_output`` lets the audit UI pinpoint whether
#: a failure happened during prompt construction, the model call, or output
#: validation.
_STEP_LOAD_CONTEXT = "load_context"
_STEP_BUILD_PROMPT_CONTEXT = "build_prompt_context"
_STEP_CALL_MODEL = "call_model"
_STEP_VALIDATE_MODEL_OUTPUT = "validate_model_output"
_STEP_PERSIST_OUTPUTS = "persist_outputs"
_STEP_COMPLETE_RUN = "complete_run"

#: The artifact_type written for every successful analysis.
_ARTIFACT_TYPE = "jd_analysis"


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
    ``parsed_facts``. When no facts have been extracted yet (e.g. unsupported
    format, or a resume uploaded before extraction shipped), ``facts`` is an
    empty dict so the prompt degrades gracefully to reasoning over ``raw_text``
    only (design §7: facts may be absent for MVP).
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


def load_jd_analysis_context(
    db: Session,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
) -> JdAnalysisContext:
    """Load and verify all sources, returning a ready ``JdAnalysisContext``.

    Raises ``HTTPException`` (404/422) on ownership or data-quality failures.
    Does not call the model and does not persist anything.
    """
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        _log.info("jd_analysis.job_not_found", user_id=current_user.id, job_id=job_id)
        raise HTTPException(status_code=404, detail="job not found")

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        _log.info(
            "jd_analysis.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        # The version row existed but its parent resume is not owned by the
        # current user. Treat as not-found to avoid revealing existence.
        _log.info(
            "jd_analysis.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
            resume_id=version.resume_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        _log.info(
            "jd_analysis.resume_version_no_text",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=422, detail="resume version has no parsed text")

    return JdAnalysisContext(
        user_id=current_user.id,
        profile=_profile_to_dict(current_user),
        job=_job_to_dict(job),
        resume=_resume_to_dict(resume, version),
    )


# ---------------------------------------------------------------------------
# Workflow orchestration (Phase 4)
# ---------------------------------------------------------------------------


def _build_summary(output: Any) -> str:
    """Compose the ``JobAnalysis.summary`` text from the validated output.

    Design §9 maps ``summary`` ← ``role_summary`` plus a concise recommendation
    summary. The responsibilities/risk-points are intentionally NOT inlined so
    the column stays a short headline; the full structured output lives on the
    linked ``GeneratedArtifact``.
    """
    parts = [output.role_summary.strip()]
    rec = getattr(output, "recommendation", None)
    if rec:
        parts.append(f"recommendation={rec}")
    return " | ".join(p for p in parts if p)


def _build_source_ids(
    *,
    user_id: str,
    job_id: str,
    resume_id: str,
    resume_version_id: str,
    execution: JdAnalysisExecution,
) -> dict[str, Any]:
    """Assemble the ``GeneratedArtifact.source_ids`` provenance dict.

    Per design §4/§5.1 the artifact carries full source provenance: who/what
    the analysis was for, which resume version fed it, and which model
    produced it. Raw resume text or full prompts are deliberately NOT included
    (design Phase 4 review checklist).
    """
    source_ids: dict[str, Any] = {
        "user_id": user_id,
        "job_id": job_id,
        "resume_id": resume_id,
        "resume_version_id": resume_version_id,
        "model_request_id": execution.request_id,
        "provider": execution.provider,
        "model": execution.model,
        "prompt_version": PROMPT_VERSION,
        "workflow_type": WORKFLOW_TYPE,
    }
    if execution.usage:
        source_ids["usage"] = execution.usage
    return source_ids


async def run_resume_aware_jd_analysis(
    db: Session,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
    gateway: ModelGateway,
) -> tuple[AgentRun, JobAnalysis, GeneratedArtifact, JdAnalysisExecution]:
    """Drive the resume-aware JD analysis workflow end to end.

    .. deprecated:: queue-migration

       Kept only for synchronous callers and tests that still exercise the
       in-process path. New callers should create a ``queued`` ``AgentRun``
       and either call :func:`_execute_jd_analysis` directly (in-process) or
       enqueue a :class:`~app.queue.payloads.ResumeAwareJdAnalysisPayload` so
       the worker handler runs :func:`run_resume_aware_jd_analysis_worker`.

    Fixed steps (design.md read APIs / R3):

    1. ``load_context`` — verify ownership and build ``JdAnalysisContext``
       (raises 404/422 directly, no run persisted since the failure predates
       the workflow transaction).
    2. ``build_prompt_context`` — assemble the chat messages + truncation metadata.
    3. ``call_model`` — send the chat request via the gateway.
    4. ``validate_model_output`` — parse + schema-validate the response. On
       :class:`JdAnalysisValidationError` a FAILED ``AgentRun`` + ``AgentStep``
       are persisted and HTTP 502 ``model returned invalid analysis`` is raised.
    5. ``persist_outputs`` — create ``JobAnalysis`` + ``GeneratedArtifact`` and
       mark the run + step as succeeded.
    6. ``complete_run`` — finalize run status and metadata.

    Returns ``(agent_run, analysis, artifact, execution)`` on success. The
    caller (route handler) maps these into the ``RunJdAnalysisResponse``.
    """
    started_at = datetime.now(UTC)

    # The run row is created only once context loading succeeds: a missing or
    # cross-user job/resume is a request-level error (404/422) and must not
    # leave a half-started run behind. This matches the design §9 ordering
    # ("load_context succeeded" is the first step, not the run creation).
    context = load_jd_analysis_context(db, current_user, job_id, resume_version_id)

    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=started_at,
        job_id=job_id,
    )

    try:
        execution = await _execute_jd_analysis(
            db=db,
            run=run,
            current_user=current_user,
            job_id=job_id,
            resume_version_id=resume_version_id,
            context=context,
            gateway=gateway,
            raise_on_failure=True,
        )
    except HTTPException:
        db.rollback()
        raise

    # The orchestrator persisted analysis/artifact inside _execute_jd_analysis;
    # re-read them so the caller gets refreshed ORM objects with stable IDs.
    analysis = job_analysis_repo.list_for_job(db, job_id, page=1, page_size=1)[0][0]
    artifact = generated_artifact_repo.get_latest_for_run(db, run.id, _ARTIFACT_TYPE)
    db.refresh(run)
    db.refresh(analysis)
    db.refresh(artifact)
    return run, analysis, artifact, execution


async def _execute_jd_analysis(
    db: Session,
    run: AgentRun,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
    context: JdAnalysisContext,
    gateway: ModelGateway,
    *,
    raise_on_failure: bool = True,
) -> JdAnalysisExecution:
    """Drive the fixed-step JD analysis orchestration using an existing run.

    Shared by the synchronous entry point
    (:func:`run_resume_aware_jd_analysis`, ``raise_on_failure=True``) and the
    queue worker (:func:`run_resume_aware_jd_analysis_worker`,
    ``raise_on_failure=False``). The caller is responsible for creating the
    ``AgentRun`` and loading ``context``; this function owns steps 1–6:

    1. ``load_context`` — record the already-verified context as a succeeded step.
    2. ``build_prompt_context`` — assemble the chat messages + truncation metadata.
    3. ``call_model`` — send the chat request via the gateway. A gateway/provider
       error fails the run with a sanitized step and (when ``raise_on_failure``)
       raises HTTP 502.
    4. ``validate_model_output`` — parse + schema-validate the response. On
       :class:`JdAnalysisValidationError` a failed step + failed run are persisted
       (sanitized) and (when ``raise_on_failure``) HTTP 502 is raised.
    5. ``persist_outputs`` — create ``JobAnalysis`` + ``GeneratedArtifact``.
    6. ``complete_run`` — finalize run status and metadata.

    With ``raise_on_failure=False`` (worker path) failures return a placeholder
    :class:`JdAnalysisExecution` instead of raising; the run is already marked
    ``failed`` on disk so the worker simply stops.

    All persisted ``AgentStep``/``AgentRun`` results are sanitized — no raw JD or
    resume text crosses into the stored audit trail (design Phase 4 checklist).
    """
    executor = JdAnalysisExecutor(gateway)

    def _fail_run(step_no: int, step_name: str, *, error: str, result: dict[str, Any]) -> None:
        """Persist a failed step + failed run, then commit (sanitized)."""
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
        db.commit()

    # Step 1 — load_context succeeded (ownership + raw-text checks already done
    # by the caller; recording the step keeps the run history self-describing).
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=1,
        name=_STEP_LOAD_CONTEXT,
        status="succeeded",
        result={
            "job_id": job_id,
            "resume_version_id": resume_version_id,
            "resume_id": context.resume.get("resume_id"),
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

    # Step 3 — call_model. A gateway/provider error is treated as a failed run
    # too (R4): the user must see a durable failed trail, not just an exception.
    try:
        response = await executor.call_model(prompt.messages)
    except Exception as exc:
        _log.warning(
            "jd_analysis.model_call_failed",
            run_id=run.id,
            provider=gateway.provider_name,
            error=str(exc),
        )
        _fail_run(
            3,
            _STEP_CALL_MODEL,
            error="model call failed",
            result={
                "provider": gateway.provider_name,
                "error_type": type(exc).__name__,
            },
        )
        if raise_on_failure:
            raise HTTPException(status_code=502, detail="model call failed") from exc
        return _failed_execution()

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=3,
        name=_STEP_CALL_MODEL,
        status="succeeded",
        result={
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
            "latency_ms": response.latency_ms,
        },
    )

    # Step 4 — validate_model_output. On validation failure persist a failed
    # step + failed run (sanitized error, no raw prompt/resume content) and
    # raise 502 so the caller does not create JobAnalysis/GeneratedArtifact.
    try:
        output = executor.validate(response)
    except JdAnalysisValidationError as exc:
        _log.warning(
            "jd_analysis.model_invalid",
            run_id=run.id,
            kind=exc.kind,
            request_id=exc.request_id,
            provider=response.provider,
        )
        _fail_run(
            4,
            _STEP_VALIDATE_MODEL_OUTPUT,
            error="model returned invalid analysis",
            result={"kind": exc.kind, "request_id": exc.request_id},
        )
        if raise_on_failure:
            raise HTTPException(status_code=502, detail="model returned invalid analysis") from exc
        return _failed_execution()

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=4,
        name=_STEP_VALIDATE_MODEL_OUTPUT,
        status="succeeded",
        result={
            "kind": "schema_valid",
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
        },
    )

    execution = JdAnalysisExecution(
        output=output,
        truncation=prompt.truncation,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        usage=_usage_to_dict(response.usage),
    )

    # Step 5 — persist_outputs. The validated output is the persistence gate:
    # only after this block do JobAnalysis / GeneratedArtifact exist.
    resume_id = str(context.resume.get("resume_id") or "")

    analysis = job_analysis_repo.create(
        db,
        job_id=job_id,
        agent_run_id=run.id,
        match_score=float(output.match_score) if output.match_score is not None else None,
        risk_score=float(output.risk_score) if output.risk_score is not None else None,
        summary=_build_summary(output),
        salary_analysis={"note": output.salary_note},
        growth_analysis={"note": output.growth_note},
        stability_analysis={"note": output.stability_note},
    )

    source_ids = _build_source_ids(
        user_id=current_user.id,
        job_id=job_id,
        resume_id=resume_id,
        resume_version_id=resume_version_id,
        execution=execution,
    )

    artifact = generated_artifact_repo.create(
        db,
        artifact_type=_ARTIFACT_TYPE,
        content=output.model_dump_json(),
        user_id=current_user.id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        agent_run_id=run.id,
        source_ids=source_ids,
        prompt_version=PROMPT_VERSION,
        model_name=execution.model,
    )

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=5,
        name=_STEP_PERSIST_OUTPUTS,
        status="succeeded",
        result={
            "analysis_id": analysis.id,
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
            "job_id": job_id,
            "resume_version_id": resume_version_id,
            "resume_id": resume_id,
            "analysis_id": analysis.id,
            "artifact_id": artifact.id,
            "provider": execution.provider,
            "model": execution.model,
            "model_request_id": execution.request_id,
            "prompt_version": PROMPT_VERSION,
            "source_context": {
                "truncation": execution.truncation,
            },
        },
    )

    db.commit()
    return execution


def _failed_execution() -> JdAnalysisExecution:
    """Return a placeholder execution for the worker (no-output) failure path."""
    return JdAnalysisExecution(
        output=None,  # type: ignore[arg-type]
        truncation={},
        provider="",
        model="",
        request_id="",
        latency_ms=0,
        usage=None,
    )


async def run_resume_aware_jd_analysis_worker(
    job_id: str,
    resume_version_id: str,
    user_id: str,
    agent_run_id: str,
    gateway: ModelGateway,
) -> None:
    """Queue worker entry point — executes analysis in the worker process.

    Opens its own DB session (never reuses a request-scoped ``Session``),
    re-loads + ownership-checks the job/resume/version, re-loads the queued
    ``AgentRun`` by ID, flips it to ``running``, and delegates to
    :func:`_execute_jd_analysis` for the fixed-step orchestration (model call
    → validation → sanitized step/run persistence → ``JobAnalysis`` /
    ``GeneratedArtifact``). Any unexpected error is caught by the handler
    wrapper (``queue.handlers.resume_aware_jd_analysis``), which calls
    :func:`fail_run` to persist a sanitized ``failed`` status so the run never
    stays stuck on ``running``.

    The ``gateway`` is constructed inside the worker via
    :func:`app.models_gateway.factory.get_model_gateway` so no request-scoped
    dependency is passed across the queue boundary.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        run = agent_run_repo.get_run(db, agent_run_id)
        if run is None:
            _log.warning(
                "jd_analysis.worker.skip",
                job_id=job_id,
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="agent run not found",
            )
            return

        if run.status in {"succeeded", "failed"}:
            _log.info(
                "jd_analysis.worker.skip_terminal_run",
                job_id=job_id,
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                status=run.status,
            )
            return

        job = db.get(JobPosting, job_id)
        if job is None or job.user_id != user_id:
            _log.warning(
                "jd_analysis.worker.skip",
                job_id=job_id,
                agent_run_id=agent_run_id,
                reason="job not found or not owned",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="job not found or not owned",
            )
            db.commit()
            return

        version = db.get(ResumeVersion, resume_version_id)
        if version is None:
            _log.warning(
                "jd_analysis.worker.skip",
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="resume version not found",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="resume version not found",
            )
            db.commit()
            return
        resume = resume_repo.get(db, version.resume_id)
        if resume is None or resume.user_id != user_id:
            _log.warning(
                "jd_analysis.worker.skip",
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="resume not found or not owned",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="resume version not found",
            )
            db.commit()
            return

        raw_text = (version.raw_text or "").strip()
        if not raw_text:
            _log.warning(
                "jd_analysis.worker.skip",
                resume_version_id=resume_version_id,
                agent_run_id=agent_run_id,
                reason="no parsed text",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="resume version has no parsed text",
            )
            db.commit()
            return

        # Flip queued → running on entry (no-op if already running from a retry).
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        agent_run_repo.update_status(db, run, status="running")
        db.commit()

        # Reconstruct a minimal UserProfile for the context loader. The loader
        # only reads id + constraints; it does not persist, so a read-only
        # projection is safe.
        profile = db.get(UserProfile, user_id)
        if profile is None:
            _log.warning(
                "jd_analysis.worker.skip",
                agent_run_id=agent_run_id,
                reason="user profile not found",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="user profile not found",
            )
            db.commit()
            return

        context = JdAnalysisContext(
            user_id=user_id,
            profile=_profile_to_dict(profile),
            job=_job_to_dict(job),
            resume=_resume_to_dict(resume, version),
        )

        try:
            await _execute_jd_analysis(
                db=db,
                run=run,
                current_user=profile,
                job_id=job_id,
                resume_version_id=resume_version_id,
                context=context,
                gateway=gateway,
                raise_on_failure=False,
            )
        except HTTPException as exc:
            # _execute_jd_analysis already persisted a failed run + step; just
            # log so the handler's catch-all does not double-fail it.
            _log.warning(
                "jd_analysis.worker.http_error",
                job_id=job_id,
                agent_run_id=agent_run_id,
                detail=exc.detail,
            )
        except Exception as exc:  # noqa: BLE001 — never let the worker crash unseen
            _log.warning(
                "jd_analysis.worker.error",
                job_id=job_id,
                agent_run_id=agent_run_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            # _execute_jd_analysis may have committed a partial failed run; if
            # the run is still non-terminal, flip it to failed here.
            db.rollback()
            fresh_run = agent_run_repo.get_run(db, agent_run_id)
            if fresh_run is not None and fresh_run.status not in {"succeeded", "failed"}:
                agent_run_repo.update_status(
                    db,
                    fresh_run,
                    status="failed",
                    finished_at=datetime.now(UTC),
                    error="jd analysis failed",
                )
                db.commit()
    finally:
        db.close()
