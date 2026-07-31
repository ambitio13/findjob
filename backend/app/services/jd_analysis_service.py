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

    executor = JdAnalysisExecutor(gateway)

    def _fail_run(step_no: int, step_name: str, *, error: str, result: dict[str, Any]) -> None:
        """Persist a failed step + failed run, commit, then re-raise as 502.

        Centralizes the failure-trail contract (design.md Failure Handling /
        R4): the failing step records a sanitized result + error message, the
        run flips to ``failed`` with sanitized metadata, and no analysis /
        artifact rows are created.
        """
        agent_run_repo.add_step(
            db,
            run_id=run.id,
            step_no=step_no,
            name=step_name,
            status="failed",
            result=result,
            error=error,
        )
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error=error,
            result={
                "job_id": job_id,
                "resume_version_id": resume_version_id,
                "failure": "model_invalid",
                **result,
            },
        )
        db.commit()

    # Step 1 — load_context succeeded (ownership + raw-text checks already done
    # above; recording the step keeps the run history self-describing).
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
        raise HTTPException(status_code=502, detail="model call failed") from exc

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
        raise HTTPException(status_code=502, detail="model returned invalid analysis") from exc

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
    db.refresh(run)
    db.refresh(analysis)
    db.refresh(artifact)
    return run, analysis, artifact, execution
