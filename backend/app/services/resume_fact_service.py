"""Resume fact extraction service — workflow orchestration.

This module owns the resume fact extraction workflow. It mirrors the JD-analysis
orchestration pattern (``jd_analysis_service.py:232-504``) but is scoped to a
resume version instead of a job:

- :func:`extract_resume_facts_with_run`: the deterministic orchestration
  transaction. It accepts an *already-created* ``AgentRun``
  (``workflow_type="resume_fact_extraction"``, status ``queued`` or
  ``running``), flips it to ``running`` on entry, drives the fixed steps
  (``load_context`` → ``build_prompt_context`` → ``call_model`` →
  ``validate_model_output`` → ``persist_outputs`` → ``complete_run``),
  persists ``AgentStep`` rows, writes the typed ``facts`` + ``_extraction``
  status block into ``ResumeVersion.parsed_facts``, and translates failures
  into a recoverable extraction status.
- :func:`extract_resume_facts`: legacy synchronous entry point that creates the
  run itself then delegates to :func:`extract_resume_facts_with_run`. Kept for
  backward compatibility with service-level tests that exercise the
  orchestration directly without going through the queue.

Ownership / failure contract (design.md):

- The resume/version must belong to ``current_user`` (404 on cross-user, raised
  by the caller before this service is called).
- Unsupported format / empty ``raw_text`` → no run created,
  ``_extraction.status = "not_run"``.
- Model/provider/schema failure on the upload path → failed ``AgentRun`` +
  ``AgentStep`` persisted (sanitized), ``_extraction.status = "failed"``,
  upload still returns 201 with the saved resume.
- Model/provider/schema failure on the explicit re-extract path → same failed
  run persisted, then HTTP 502 raised (extraction is the primary action).

Step results are sanitized (counts, provider/model/prompt_version, latency,
validation status) — never raw resume text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.prompts.resume_fact import PROMPT_VERSION
from app.agents.resume_fact_executor import (
    ResumeFactExecution,
    ResumeFactExecutor,
    ResumeFactValidationError,
    _usage_to_dict,
)
from app.core.logging import get_logger
from app.db.models.models import Resume, ResumeVersion, UserProfile
from app.db.repositories import agent_run_repo
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway

_log = get_logger("app.services.resume_fact_service")

#: The fixed workflow type stored on ``AgentRun.workflow_type``.
WORKFLOW_TYPE = "resume_fact_extraction"

#: Fixed step names produced by the workflow. The order matches the step_no
#: persisted on each ``AgentStep``. Splitting the coarse model call into
#: ``build_prompt_context`` / ``call_model`` / ``validate_model_output`` lets
#: the audit UI pinpoint whether a failure happened during prompt construction,
#: the model call, or output validation.
_STEP_LOAD_CONTEXT = "load_context"
_STEP_BUILD_PROMPT_CONTEXT = "build_prompt_context"
_STEP_CALL_MODEL = "call_model"
_STEP_VALIDATE_MODEL_OUTPUT = "validate_model_output"
_STEP_PERSIST_OUTPUTS = "persist_outputs"
_STEP_COMPLETE_RUN = "complete_run"

#: All lifecycle states for ``parsed_facts._extraction.status``.
#:
#: - ``pending``: upload saved, extraction scheduled but not started.
#: - ``running``: extraction run started (written by the background runner
#:   before the model call).
#: - ``succeeded``: facts written.
#: - ``failed``: extraction failed after upload.
#: - ``needs_confirmation``: legacy value preserved for older rows.
#: - ``not_run``: no extractable raw text / unsupported parser result.
ExtractionStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "needs_confirmation",
    "not_run",
]

#: Terminal statuses — once reached the frontend can stop polling.
TERMINAL_EXTRACTION_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "needs_confirmation", "not_run"}
)


class ExtractionOutcome:
    """Lightweight result carrier returned to upload/re-extract callers.

    Carries the final extraction status and run id without forcing the caller to
    re-read ``parsed_facts``. The ``AgentRun`` row (when created) is also
    returned so the API layer can surface run details.
    """

    def __init__(self, status: ExtractionStatus, run: AgentRun | None) -> None:
        self.status = status
        self.run = run


async def extract_resume_facts_with_run(
    db: Session,
    run: AgentRun,
    *,
    resume: Resume,
    version: ResumeVersion,
    gateway: ModelGateway,
    raise_on_failure: bool = False,
) -> ExtractionOutcome:
    """Drive the resume fact extraction workflow using an *existing* ``AgentRun``.

    This is the worker entry point. The API layer (or a test) creates the
    ``AgentRun`` row with ``status="queued"`` before calling this function; this
    function flips it to ``running`` on entry. Fixed steps:

    1. ``load_context`` — confirm ``raw_text`` is non-empty; record the step.
    2. ``build_prompt_context`` — assemble the chat messages + truncation.
    3. ``call_model`` — send the chat request via the gateway.
    4. ``validate_model_output`` — parse + schema-validate the response. On
       :class:`ResumeFactValidationError` a FAILED ``AgentRun`` + ``AgentStep``
       are persisted and (for re-extract) HTTP 502 is raised.
    5. ``persist_outputs`` — write typed ``facts`` + ``_extraction`` status into
       ``version.parsed_facts`` (preserving existing ``_parser``/``_parser_status``).
    6. ``complete_run`` — finalize run status and metadata.

    ``raise_on_failure`` selects the caller contract:

    - ``False`` (upload path): model failure persists a failed run, writes
      ``_extraction.status = "failed"``, and returns the outcome. Upload still
      returns 201.
    - ``True`` (re-extract path): same failed-run persistence, then HTTP 502 is
      raised so the explicit extraction action surfaces the failure.

    Step results are sanitized (counts, provider/model/prompt_version, latency,
    validation status) — never raw resume text.
    """
    raw_text = (version.raw_text or "").strip()

    # Unsupported format / empty raw_text: no run created, status not_run.
    # This mirrors the JD-analysis 422 path but stays non-fatal for upload.
    if not raw_text:
        _write_extraction_status(db, version, status="not_run")
        db.commit()
        return ExtractionOutcome(status="not_run", run=None)

    # Flip queued → running on entry (no-op if already running from a retry).
    if run.started_at is None:
        run.started_at = datetime.now(UTC)
    agent_run_repo.update_status(db, run, status="running")
    db.flush()

    # Record the run id on the _extraction block now that it exists, so the
    # UI can link to /agent-runs/{run_id}/detail while extraction is running.
    _write_extraction_status(
        db,
        version,
        status="running",
        run_id=run.id,
        provider=gateway.provider_name,
    )
    db.commit()

    executor = ResumeFactExecutor(gateway)
    filename = resume.filename or ""

    def _fail_run(step_no: int, step_name: str, *, error: str, result: dict[str, Any]) -> None:
        """Persist a failed step + failed run, commit (sanitized).

        Mirrors ``jd_analysis_service._fail_run``. The failing step records a
        sanitized result + error, the run flips to ``failed`` with sanitized
        metadata, and no ``facts`` are written.
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
                "resume_id": resume.id,
                "resume_version_id": version.id,
                "failure": "extraction_failed",
                **result,
            },
        )
        _write_extraction_status(
            db,
            version,
            status="failed",
            run_id=run.id,
            provider=gateway.provider_name,
        )
        db.commit()

    # Step 1 — load_context succeeded (raw_text non-empty checked above).
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=1,
        name=_STEP_LOAD_CONTEXT,
        status="succeeded",
        result={
            "resume_id": resume.id,
            "resume_version_id": version.id,
            "raw_text_len": len(raw_text),
        },
    )

    # Step 2 — build_prompt_context.
    prompt = executor.build_prompt(raw_text, filename)
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

    # Step 3 — call_model. A gateway/provider error is treated as a failed run.
    try:
        response = await executor.call_model(prompt.messages)
    except Exception as exc:
        _log.warning(
            "resume_fact.model_call_failed",
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
        return ExtractionOutcome(status="failed", run=run)

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
    # step + failed run (sanitized, no raw resume content).
    try:
        output = executor.validate(response)
    except ResumeFactValidationError as exc:
        _log.warning(
            "resume_fact.model_invalid",
            run_id=run.id,
            kind=exc.kind,
            request_id=exc.request_id,
            provider=response.provider,
        )
        _fail_run(
            4,
            _STEP_VALIDATE_MODEL_OUTPUT,
            error="model returned invalid resume facts",
            result={"kind": exc.kind, "request_id": exc.request_id},
        )
        if raise_on_failure:
            raise HTTPException(
                status_code=502, detail="model returned invalid resume facts"
            ) from exc
        return ExtractionOutcome(status="failed", run=run)

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

    execution = ResumeFactExecution(
        output=output,
        truncation=prompt.truncation,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        usage=_usage_to_dict(response.usage),
    )

    # Step 5 — persist_outputs. Write typed `facts` + `_extraction` status into
    # parsed_facts, preserving existing telemetry keys.
    facts_dict = output.model_dump(mode="json")
    _write_facts(
        db,
        version,
        facts=facts_dict,
        status="succeeded",
        run_id=run.id,
        prompt_version=PROMPT_VERSION,
        provider=execution.provider,
        model=execution.model,
    )

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=5,
        name=_STEP_PERSIST_OUTPUTS,
        status="succeeded",
        result={
            "resume_version_id": version.id,
            "facts_written": True,
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
            "resume_id": resume.id,
            "resume_version_id": version.id,
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
    return ExtractionOutcome(status="succeeded", run=run)


async def extract_resume_facts(
    db: Session,
    resume: Resume,
    version: ResumeVersion,
    gateway: ModelGateway,
    *,
    raise_on_failure: bool = False,
) -> ExtractionOutcome:
    """Legacy synchronous entry point — creates the ``AgentRun`` then delegates.

    Creates an ``AgentRun`` with ``status="running"`` and delegates to
    :func:`extract_resume_facts_with_run`. Kept for backward compatibility with
    service-level tests that exercise the orchestration directly without going
    through the queue.
    """
    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        # Unsupported / empty raw text: no run created, status not_run.
        _write_extraction_status(db, version, status="not_run")
        db.commit()
        return ExtractionOutcome(status="not_run", run=None)

    started_at = datetime.now(UTC)
    run = agent_run_repo.create_run(
        db,
        user_id=resume.user_id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=started_at,
    )
    return await extract_resume_facts_with_run(
        db,
        run,
        resume=resume,
        version=version,
        gateway=gateway,
        raise_on_failure=raise_on_failure,
    )


# ---------------------------------------------------------------------------
# parsed_facts mutation helpers
# ---------------------------------------------------------------------------


def _current_facts(version: ResumeVersion) -> dict[str, Any]:
    """Return a mutable copy of ``version.parsed_facts`` (never None)."""
    return dict(version.parsed_facts or {})


def _write_extraction_status(
    db: Session,
    version: ResumeVersion,
    *,
    status: ExtractionStatus,
    run_id: str | None = None,
    provider: str | None = None,
) -> None:
    """Write/overwrite only the ``_extraction`` status block.

    Used for the ``not_run`` and ``failed`` paths where no ``facts`` object is
    written. Preserves existing ``_parser``/``_parser_status`` telemetry.
    """
    facts = _current_facts(version)
    facts["_extraction"] = _extraction_block(
        status=status,
        run_id=run_id,
        prompt_version=PROMPT_VERSION if status != "not_run" else None,
        provider=provider,
    )
    version.parsed_facts = facts
    db.flush()


def _write_facts(
    db: Session,
    version: ResumeVersion,
    *,
    facts: dict[str, Any],
    status: ExtractionStatus,
    run_id: str | None,
    prompt_version: str,
    provider: str | None,
    model: str | None,
) -> None:
    """Write the typed ``facts`` object + ``_extraction`` status block.

    Preserves existing ``_parser``/``_parser_status`` telemetry keys. The
    ``facts`` key is the validated ``ResumeFactsModelOutput.model_dump``.
    """
    current = _current_facts(version)
    current["facts"] = facts
    current["_extraction"] = _extraction_block(
        status=status,
        run_id=run_id,
        prompt_version=prompt_version,
        provider=provider,
        model=model,
    )
    version.parsed_facts = current
    db.flush()


def _extraction_block(
    *,
    status: ExtractionStatus,
    run_id: str | None,
    prompt_version: str | None,
    provider: str | None,
    model: str | None = None,
) -> dict[str, Any]:
    """Assemble the ``_extraction`` status sub-dict (design.md parsed_facts)."""
    block: dict[str, Any] = {
        "status": status,
        "extracted_at": datetime.now(UTC).isoformat(),
    }
    if run_id is not None:
        block["run_id"] = run_id
    if prompt_version is not None:
        block["prompt_version"] = prompt_version
    if provider is not None:
        block["provider"] = provider
    if model is not None:
        block["model"] = model
    return block


# ---------------------------------------------------------------------------
# Upload-time scheduling + worker runner
# ---------------------------------------------------------------------------


def mark_extraction_pending(
    db: Session,
    version: ResumeVersion,
    *,
    run_id: str | None = None,
) -> None:
    """Write ``_extraction.status="pending"`` on a freshly saved version.

    Called by the upload endpoint after the resume/version are committed and
    before the extraction job is enqueued to the worker queue. Preserves
    existing ``_parser``/``_parser_status`` telemetry. The optional ``run_id``
    links the pending status to the queued ``AgentRun`` created before enqueue
    so the UI can link to ``/agent-runs/{run_id}`` while polling.
    """
    _write_extraction_status(db, version, status="pending", run_id=run_id)
    db.commit()


def mark_extraction_not_run(
    db: Session,
    version: ResumeVersion,
) -> None:
    """Write ``_extraction.status="not_run"`` for unsupported/empty raw text.

    Called by the upload endpoint when there is no extractable raw text so the
    detail view immediately shows a terminal status without polling.
    """
    _write_extraction_status(db, version, status="not_run")
    db.commit()


def mark_extraction_failed(
    db: Session,
    version: ResumeVersion,
    *,
    run_id: str | None = None,
) -> None:
    """Write ``_extraction.status="failed"`` for queue submission failures.

    Used when the API has already created a queued ``AgentRun`` and linked it
    to the resume version, but Redis enqueue fails before a worker can pick up
    the job. Without this transition the frontend would keep polling a
    permanently pending extraction status.
    """
    _write_extraction_status(db, version, status="failed", run_id=run_id)
    db.commit()


async def run_resume_fact_extraction_worker(
    resume_id: str,
    version_id: str,
    user_id: str,
    agent_run_id: str,
    gateway: ModelGateway,
) -> None:
    """Queue worker entry point — executes extraction in the worker process.

    Opens its own DB session (never reuses a request-scoped ``Session``),
    re-loads + ownership-checks the resume/version, re-loads the queued
    ``AgentRun`` by ID, and delegates to :func:`extract_resume_facts_with_run`
    for the fixed-step orchestration (model call → validation → sanitized
    step/run persistence). Any unexpected error is caught by the handler
    wrapper (``queue.handlers.resume_fact_extraction``), which calls
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
                "resume_fact.worker.skip",
                resume_id=resume_id,
                version_id=version_id,
                agent_run_id=agent_run_id,
                reason="agent run not found",
            )
            return

        resume = db.get(Resume, resume_id)
        if resume is None or resume.user_id != user_id:
            _log.warning(
                "resume_fact.worker.skip",
                resume_id=resume_id,
                version_id=version_id,
                agent_run_id=agent_run_id,
                reason="resume not found or not owned",
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="resume not found or not owned",
            )
            db.commit()
            return
        version = db.get(ResumeVersion, version_id)
        if version is None or version.resume_id != resume.id:
            _log.warning(
                "resume_fact.worker.skip",
                resume_id=resume_id,
                version_id=version_id,
                agent_run_id=agent_run_id,
                reason="version not found",
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
            # No extractable text — write the canonical not_run status and flip
            # the run to failed (it was created queued but cannot execute).
            _write_extraction_status(db, version, status="not_run")
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="no extractable raw text",
            )
            db.commit()
            return

        try:
            await extract_resume_facts_with_run(
                db, run, resume=resume, version=version, gateway=gateway
            )
        except HTTPException:
            # raise_on_failure defaults to False so 502 is never raised here,
            # but guard anyway: a failed run is already persisted by
            # extract_resume_facts_with_run, so just log and keep the failed
            # status.
            _log.warning(
                "resume_fact.worker.http_error",
                resume_id=resume_id,
                version_id=version_id,
                agent_run_id=agent_run_id,
            )
        except Exception as exc:  # noqa: BLE001 — never let the worker crash unseen
            _log.warning(
                "resume_fact.worker.error",
                resume_id=resume_id,
                version_id=version_id,
                agent_run_id=agent_run_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            # extract_resume_facts_with_run normally persists `failed` itself;
            # this guard covers any error before/after its own commit so the
            # row never stays stuck on `running`.
            _write_extraction_status(
                db,
                version,
                status="failed",
                run_id=run.id,
                provider=gateway.provider_name,
            )
            agent_run_repo.update_status(
                db,
                run,
                status="failed",
                finished_at=datetime.now(UTC),
                error="resume fact extraction failed",
            )
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Re-extract entry point (used by the API layer)
# ---------------------------------------------------------------------------


def load_resume_version_for_user(
    db: Session,
    current_user: UserProfile,
    resume_id: str,
    version_id: str,
) -> tuple[Resume, ResumeVersion]:
    """Load and verify ownership of a resume + version.

    Raises ``HTTPException`` (404) when the resume or version is missing or
    belongs to another user, mirroring the cross-user 404 convention used
    throughout the API.
    """
    from app.db.repositories import resume_repo

    resume = resume_repo.get(db, resume_id)
    if resume is None or resume.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="resume not found")

    version = db.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume.id:
        raise HTTPException(status_code=404, detail="resume version not found")

    return resume, version
