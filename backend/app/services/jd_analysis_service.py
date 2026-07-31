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

#: Fixed step names produced by the planner (design.md §3). The order matches
#: the step_no persisted on each ``AgentStep``.
_STEP_LOAD_CONTEXT = "load_context"
_STEP_ANALYZE_WITH_MODEL = "analyze_with_model"
_STEP_PERSIST_OUTPUTS = "persist_outputs"

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
    """Project a ``UserProfile`` into the compact profile dict for the prompt."""
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
        "constraints": profile.constraints,
    }


def _resume_to_dict(resume: Resume, version: ResumeVersion) -> dict[str, Any]:
    """Project a resume + version into the resume dict for the prompt."""
    facts = version.parsed_facts or {}
    return {
        "resume_id": resume.id,
        "resume_version_id": version.id,
        "filename": resume.filename,
        "parser_status": facts.get("_parser_status"),
        "parser_name": facts.get("_parser"),
        "raw_text": version.raw_text or "",
        "parsed_facts": facts,
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

    Fixed steps (design.md §3 / §9):

    1. ``load_context`` — verify ownership and build ``JdAnalysisContext``
       (raises 404/422 directly, no run persisted since the failure predates
       the workflow transaction).
    2. ``analyze_with_model`` — call the model via the executor. On
       :class:`JdAnalysisValidationError` a FAILED ``AgentRun`` + ``AgentStep``
       are persisted and HTTP 502 ``model returned invalid analysis`` is raised.
    3. ``persist_outputs`` — create ``JobAnalysis`` + ``GeneratedArtifact`` and
       mark the run + step as succeeded.

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
    )

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

    # Step 2 — analyze_with_model. On validation failure we persist a failed
    # step + failed run (sanitized error, no raw prompt/resume content) and
    # raise 502 so the caller does not create JobAnalysis/GeneratedArtifact.
    executor = JdAnalysisExecutor(gateway)
    try:
        execution = await executor.execute(context)
    except JdAnalysisValidationError as exc:
        _log.warning(
            "jd_analysis.model_invalid",
            run_id=run.id,
            kind=exc.kind,
            request_id=exc.request_id,
            provider=gateway.provider_name,
        )
        agent_run_repo.add_step(
            db,
            run_id=run.id,
            step_no=2,
            name=_STEP_ANALYZE_WITH_MODEL,
            status="failed",
            result={"kind": exc.kind, "request_id": exc.request_id},
            error=str(exc),
        )
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error="model returned invalid analysis",
            result={
                "job_id": job_id,
                "resume_version_id": resume_version_id,
                "failure": "model_invalid",
                "request_id": exc.request_id,
            },
        )
        db.commit()
        raise HTTPException(status_code=502, detail="model returned invalid analysis") from exc

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=2,
        name=_STEP_ANALYZE_WITH_MODEL,
        status="succeeded",
        result={
            "provider": execution.provider,
            "model": execution.model,
            "request_id": execution.request_id,
            "latency_ms": execution.latency_ms,
        },
    )

    # Step 3 — persist_outputs. The validated output is the persistence gate:
    # only after this block do JobAnalysis / GeneratedArtifact exist.
    output = execution.output
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
        step_no=3,
        name=_STEP_PERSIST_OUTPUTS,
        status="succeeded",
        result={
            "analysis_id": analysis.id,
            "artifact_id": artifact.id,
        },
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
