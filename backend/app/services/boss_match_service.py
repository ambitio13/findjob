"""BOSS match-decision service — context loader + workflow orchestration.

This module owns the BOSS match-decision workflow:

- :func:`load_boss_match_context`: read and verify ownership of the current
  user's profile, the target job, and the selected resume version, then build a
  ``BossMatchContext`` ready for the prompt builder and executor.
- :func:`run_boss_match_decision`: the deterministic synchronous orchestration.
  It creates an ``AgentRun``, drives the fixed steps (``load_context`` →
  ``build_prompt_context`` → ``call_model`` → ``validate_model_output`` →
  ``persist_outputs`` → ``complete_run``), persists ``AgentStep`` /
  ``GeneratedArtifact`` rows, and translates failures into the API error
  contract (502 on model/validation failure).

After model validation the service applies
:func:`~app.agents.opening_message_guard.apply_match_safety_gate` to enforce
deterministic backend safety rules (low-confidence downgrade, opening-message
PII/length checks). That gate may downgrade ``communicate`` → ``needs_review``
but never produces a ``communicate`` decision from a non-communicate one.

Safety invariants:

- Cross-user access returns 404 (not 403), matching the convention used across
  the codebase.
- No raw JD/resume text is persisted in ``AgentRun.result`` or
  ``GeneratedArtifact.source_ids`` — only IDs + provider metadata.
- One ``job_id`` per match call — no batch paths.
- Low confidence and validation errors stop before any browser side effect.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.boss_match_executor import (
    BossMatchExecution,
    BossMatchExecutor,
    BossMatchValidationError,
)
from app.agents.jd_analysis_executor import _usage_to_dict
from app.agents.opening_message_guard import apply_match_safety_gate
from app.agents.prompts.boss_match import PROMPT_VERSION, BossMatchContext
from app.core.logging import get_logger
from app.db.models.models import (
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.repositories import agent_run_repo, generated_artifact_repo, resume_repo
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway

_log = get_logger("app.services.boss_match_service")

#: The fixed workflow type stored on ``AgentRun.workflow_type``.
WORKFLOW_TYPE = "boss_match_decision"

#: The artifact_type written for every successful match decision.
_ARTIFACT_TYPE = "boss_match_decision"

# Fixed step names. Splitting the model phase into build_prompt_context /
# call_model / validate_model_output lets the audit UI pinpoint where a failure
# happened during prompt construction, the model call, or output validation.
_STEP_LOAD_CONTEXT = "load_context"
_STEP_BUILD_PROMPT_CONTEXT = "build_prompt_context"
_STEP_CALL_MODEL = "call_model"
_STEP_VALIDATE_MODEL_OUTPUT = "validate_model_output"
_STEP_PERSIST_OUTPUTS = "persist_outputs"
_STEP_COMPLETE_RUN = "complete_run"


# ---------------------------------------------------------------------------
# Projection helpers (mirror jd_analysis_service so the prompt gets the same
# compact dicts without the service depending on the analysis module).
# ---------------------------------------------------------------------------


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
        "facts": facts.get("facts") or {},
    }


# ---------------------------------------------------------------------------
# Context loader
# ---------------------------------------------------------------------------


def load_boss_match_context(
    db: Session,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
) -> BossMatchContext:
    """Load and verify all sources, returning a ready ``BossMatchContext``.

    Raises ``HTTPException`` (404/422) on ownership or data-quality failures.
    Does not call the model and does not persist anything.

    - job missing or not owned by the current user → 404;
    - resume version missing, or its parent resume not owned → 404;
    - resume version ``raw_text`` empty/blank → 422.
    """
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        _log.info("boss_match.job_not_found", user_id=current_user.id, job_id=job_id)
        raise HTTPException(status_code=404, detail="job not found")

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        _log.info(
            "boss_match.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        _log.info(
            "boss_match.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
            resume_id=version.resume_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        _log.info(
            "boss_match.resume_version_no_text",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=422, detail="resume version has no parsed text")

    return BossMatchContext(
        user_id=current_user.id,
        profile=_profile_to_dict(current_user),
        job=_job_to_dict(job),
        resume=_resume_to_dict(resume, version),
    )


# ---------------------------------------------------------------------------
# Workflow orchestration
# ---------------------------------------------------------------------------


def _build_source_ids(
    *,
    user_id: str,
    job_id: str,
    resume_id: str,
    resume_version_id: str,
    execution: BossMatchExecution,
    safety_downgraded: bool,
) -> dict[str, Any]:
    """Assemble the ``GeneratedArtifact.source_ids`` provenance dict.

    Raw resume text or full prompts are deliberately NOT included — only IDs
    and provider/model metadata so the artifact can be explained later without
    leaking sensitive inputs.
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
        "safety_downgraded": safety_downgraded,
    }
    if execution.usage:
        source_ids["usage"] = execution.usage
    return source_ids


async def run_boss_match_decision(
    db: Session,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
    gateway: ModelGateway,
) -> tuple[AgentRun, GeneratedArtifact, BossMatchExecution, bool]:
    """Drive the BOSS match-decision workflow end to end.

    Fixed steps:

    1. ``load_context`` — verify ownership and build ``BossMatchContext``
       (raises 404/422 directly, no run persisted since the failure predates
       the workflow transaction).
    2. ``build_prompt_context`` — assemble the chat messages + truncation metadata.
    3. ``call_model`` — send the chat request via the gateway. On gateway error
       a FAILED ``AgentRun`` + ``AgentStep`` are persisted and HTTP 502 is raised.
    4. ``validate_model_output`` — parse + schema-validate the response. On
       :class:`BossMatchValidationError` a failed step + failed run are persisted
       (sanitized) and HTTP 502 is raised. After validation the safety gate is
       applied.
    5. ``persist_outputs`` — create ``GeneratedArtifact`` and mark the run + step
       as succeeded.
    6. ``complete_run`` — finalize run status and metadata.

    Returns ``(agent_run, artifact, execution, safety_downgraded)`` on success.
    ``safety_downgraded`` is ``True`` when the safety gate changed the decision
    (e.g. ``communicate`` → ``needs_review``).
    """
    started_at = datetime.now(UTC)

    context = load_boss_match_context(db, current_user, job_id, resume_version_id)

    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=started_at,
        job_id=job_id,
    )

    try:
        execution, safety_downgraded = await _execute_boss_match(
            db=db,
            run=run,
            current_user=current_user,
            job_id=job_id,
            resume_version_id=resume_version_id,
            context=context,
            gateway=gateway,
        )
    except HTTPException:
        db.rollback()
        raise

    artifact = generated_artifact_repo.get_latest_for_run(
        db, run.id, artifact_type=_ARTIFACT_TYPE
    )
    if artifact is None:
        # Should not happen on the success path — the orchestrator just created
        # it. Treat as a 502 so the caller never sees a half-built response.
        db.rollback()
        raise HTTPException(
            status_code=502, detail="match decision artifact was not persisted"
        )
    db.refresh(run)
    db.refresh(artifact)
    return run, artifact, execution, safety_downgraded


async def _execute_boss_match(
    db: Session,
    run: AgentRun,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
    context: BossMatchContext,
    gateway: ModelGateway,
) -> tuple[BossMatchExecution, bool]:
    """Drive the fixed-step boss match orchestration using an existing run.

    Returns ``(execution, safety_downgraded)``. On failure persists a sanitized
    failed step + run and raises HTTP 502.
    """
    executor = BossMatchExecutor(gateway)

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

    # Step 1 — load_context succeeded.
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

    # Step 3 — call_model.
    try:
        response = await executor.call_model(prompt.messages)
    except Exception as exc:
        _log.warning(
            "boss_match.model_call_failed",
            agent_run_id=run.id,
            workflow_type=WORKFLOW_TYPE,
            job_id=job_id,
            user_id=current_user.id,
            provider=gateway.provider_name,
            error_type=type(exc).__name__,
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

    # Step 4 — validate_model_output + safety gate.
    try:
        model_output = executor.validate(response)
    except BossMatchValidationError as exc:
        _log.warning(
            "boss_match.model_invalid",
            agent_run_id=run.id,
            workflow_type=WORKFLOW_TYPE,
            job_id=job_id,
            user_id=current_user.id,
            failure_code="model_invalid",
            kind=exc.kind,
            request_id=exc.request_id,
            provider=response.provider,
        )
        _fail_run(
            4,
            _STEP_VALIDATE_MODEL_OUTPUT,
            error="model returned invalid match decision",
            result={"kind": exc.kind, "request_id": exc.request_id},
        )
        raise HTTPException(
            status_code=502, detail="model returned invalid match decision"
        ) from exc

    # Apply the deterministic backend safety gate. This may downgrade
    # ``communicate`` → ``needs_review`` but never produces a ``communicate``
    # decision from a non-communicate one.
    raw_decision = model_output.decision
    gated_output = apply_match_safety_gate(model_output)
    safety_downgraded = gated_output.decision != raw_decision

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
            "raw_decision": raw_decision.value,
            "gated_decision": gated_output.decision.value,
            "safety_downgraded": safety_downgraded,
        },
    )

    execution = BossMatchExecution(
        output=gated_output,
        truncation=prompt.truncation,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        usage=_usage_to_dict(response.usage),
    )

    # Step 5 — persist_outputs.
    resume_id = str(context.resume.get("resume_id") or "")

    source_ids = _build_source_ids(
        user_id=current_user.id,
        job_id=job_id,
        resume_id=resume_id,
        resume_version_id=resume_version_id,
        execution=execution,
        safety_downgraded=safety_downgraded,
    )

    artifact = generated_artifact_repo.create(
        db,
        artifact_type=_ARTIFACT_TYPE,
        content=gated_output.model_dump_json(),
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
        result={"artifact_id": artifact.id},
    )

    # Step 6 — complete_run.
    run_result: dict[str, Any] = {
        "job_id": job_id,
        "resume_version_id": resume_version_id,
        "resume_id": resume_id,
        "artifact_id": artifact.id,
        "provider": execution.provider,
        "model": execution.model,
        "model_request_id": execution.request_id,
        "prompt_version": PROMPT_VERSION,
        "decision": gated_output.decision.value,
        "score": gated_output.score,
        "safety_downgraded": safety_downgraded,
        "source_context": {
            "truncation": execution.truncation,
        },
    }
    if safety_downgraded:
        run_result["safety_message"] = (
            f"low confidence or invalid opening message: downgraded "
            f"{raw_decision.value} → {gated_output.decision.value}"
        )

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
        result=run_result,
    )

    db.commit()
    return execution, safety_downgraded


__all__ = [
    "WORKFLOW_TYPE",
    "load_boss_match_context",
    "run_boss_match_decision",
]
