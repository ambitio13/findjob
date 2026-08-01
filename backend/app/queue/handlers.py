"""Queue handler functions — the worker-side entry points.

Each handler is an async function registered under the name matching its
``WorkflowPayload.workflow_type``. The worker dispatches an enqueued job to the
handler with the same name.

Shared contract (design.md):

- A handler receives the deserialized payload dict (arq passes kwargs).
- It opens its own ``SessionLocal()`` — never a request-scoped session.
- It re-loads owned resources by ID and re-checks user ownership.
- It marks the referenced ``AgentRun`` ``running`` on entry and a terminal
  state on exit.
- :func:`fail_run` is the shared guard that flips a run to ``failed`` with a
  sanitized error message when a handler raises.

This module contains the foundation smoke handler plus workflow handlers added
by async migration tasks. Resume-aware JD analysis will be added by its
respective migration task.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.repositories import agent_run_repo
from app.db.session import SessionLocal
from app.queue.payloads import (
    JdPasteParsePayload,
    ResumeAwareJdAnalysisPayload,
    ResumeFactExtractionPayload,
    SmokePayload,
)

_log = get_logger("app.queue.handlers")


def fail_run(agent_run_id: str, *, error: str) -> None:
    """Mark ``agent_run_id`` failed with a sanitized error.

    Shared guard called when a handler raises. It opens its own short-lived
    session so it works even if the handler's session is in a bad state. The
    error string is caller-provided and should be a sanitized summary
    (e.g. ``"model call failed"``), never raw exception text that might
    contain user content.
    """
    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, agent_run_id)
        if run is None:
            _log.warning("queue.fail_run_missing", agent_run_id=agent_run_id)
            return
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error=error,
        )
        db.commit()


async def smoke(
    ctx: dict[str, Any],
    payload: SmokePayload | dict[str, Any],
) -> str:
    """Foundation smoke handler — proves the queue wiring end to end.

    Marks the referenced ``AgentRun`` succeeded without touching any model or
    domain resource. Used by queue runtime tests and as a connectivity probe.
    arq always passes the worker context as the first argument, then the
    enqueued payload as the second argument. Accepts either a typed
    :class:`SmokePayload` or the raw dict arq delivers so the worker boundary
    stays forgiving.
    """
    _ = ctx
    if isinstance(payload, dict):
        payload = SmokePayload.model_validate(payload)

    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, payload.agent_run_id)
        if run is None:
            _log.warning("queue.smoke_missing_run", agent_run_id=payload.agent_run_id)
            return "missing_run"
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        agent_run_repo.update_status(db, run, status="running")
        db.commit()

        agent_run_repo.update_status(
            db,
            run,
            status="succeeded",
            finished_at=datetime.now(UTC),
            result={"workflow": "smoke", "user_id": payload.user_id},
        )
        db.commit()
        _log.info("queue.smoke_succeeded", agent_run_id=payload.agent_run_id)
        return run.id


async def jd_paste_parsing(
    ctx: dict[str, Any],
    payload: JdPasteParsePayload | dict[str, Any],
) -> str:
    """JD paste parsing worker handler — executes the parse workflow.

    Receives the raw JD text + platform hint in the payload (there is no
    durable parent row at parse time), opens its own DB session, re-loads the
    queued ``AgentRun`` by ID, re-checks user ownership, and delegates to
    :func:`app.services.jd_parse_service.parse_jd_with_run` for the fixed-step
    orchestration (model call → validation → sanitized step/run persistence).

    The model gateway is constructed inside the worker via
    :func:`app.models_gateway.factory.get_model_gateway` so no request-scoped
    dependency is passed across the queue boundary.

    On any uncaught exception the shared :func:`fail_run` guard flips the run
    to ``failed`` with a sanitized error so it never stays stuck on
    ``running``.
    """
    _ = ctx
    if isinstance(payload, dict):
        payload = JdPasteParsePayload.model_validate(payload)

    # Construct the model gateway inside the worker process.
    from app.models_gateway.factory import get_model_gateway
    from app.services.jd_parse_service import parse_jd_with_run

    gateway = get_model_gateway()

    try:
        with SessionLocal() as db:
            run = agent_run_repo.get_run(db, payload.agent_run_id)
            if run is None:
                _log.warning(
                    "queue.jd_paste_parsing_missing_run",
                    agent_run_id=payload.agent_run_id,
                )
                return "missing_run"

            # Re-check ownership: a cross-user payload must not execute.
            if run.user_id != payload.user_id:
                _log.warning(
                    "queue.jd_paste_parsing_owner_mismatch",
                    agent_run_id=payload.agent_run_id,
                    payload_user=payload.user_id,
                    run_user=run.user_id,
                )
                fail_run(payload.agent_run_id, error="ownership mismatch")
                return "ownership_mismatch"

            await parse_jd_with_run(
                db,
                run,
                user_id=payload.user_id,
                raw_jd=payload.raw_jd,
                platform_hint=payload.platform,
                gateway=gateway,
            )
            _log.info(
                "queue.jd_paste_parsing_completed",
                agent_run_id=payload.agent_run_id,
                status=run.status,
            )
            return run.id
    except Exception as exc:  # noqa: BLE001 — sanitize and fail the run
        _log.warning(
            "queue.jd_paste_parsing_error",
            agent_run_id=payload.agent_run_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        fail_run(payload.agent_run_id, error="jd paste parsing failed")
        return "failed"


async def resume_fact_extraction(
    ctx: dict[str, Any],
    payload: ResumeFactExtractionPayload | dict[str, Any],
) -> str:
    """Resume fact extraction worker handler — executes the extraction workflow.

    Receives only durable resource IDs (``resume_id``, ``version_id``) in the
    payload — no raw resume text crosses the queue boundary. The worker
    re-reads ``ResumeVersion.raw_text`` from its own DB session, re-loads the
    queued ``AgentRun`` by ID, re-checks user ownership, and delegates to
    :func:`app.services.resume_fact_service.run_resume_fact_extraction_worker`
    for the fixed-step orchestration (model call → validation → sanitized
    step/run persistence → typed ``facts`` + ``_extraction`` status into
    ``ResumeVersion.parsed_facts``).

    The model gateway is constructed inside the worker via
    :func:`app.models_gateway.factory.get_model_gateway` so no request-scoped
    dependency is passed across the queue boundary.

    On any uncaught exception the shared :func:`fail_run` guard flips the run
    to ``failed`` with a sanitized error so it never stays stuck on
    ``running``.
    """
    _ = ctx
    if isinstance(payload, dict):
        payload = ResumeFactExtractionPayload.model_validate(payload)

    # Construct the model gateway inside the worker process.
    from app.models_gateway.factory import get_model_gateway
    from app.services.resume_fact_service import run_resume_fact_extraction_worker

    gateway = get_model_gateway()

    try:
        with SessionLocal() as db:
            run = agent_run_repo.get_run(db, payload.agent_run_id)
            if run is None:
                _log.warning(
                    "queue.resume_fact_extraction_missing_run",
                    agent_run_id=payload.agent_run_id,
                )
                return "missing_run"

            # Re-check ownership: a cross-user payload must not execute.
            if run.user_id != payload.user_id:
                _log.warning(
                    "queue.resume_fact_extraction_owner_mismatch",
                    agent_run_id=payload.agent_run_id,
                    payload_user=payload.user_id,
                    run_user=run.user_id,
                )
                fail_run(payload.agent_run_id, error="ownership mismatch")
                return "ownership_mismatch"

        # The worker runner opens its own session; the ownership check above is
        # a fast-fail guard so a cross-user payload never reaches the service.
        await run_resume_fact_extraction_worker(
            resume_id=payload.resume_id,
            version_id=payload.version_id,
            user_id=payload.user_id,
            agent_run_id=payload.agent_run_id,
            gateway=gateway,
        )
        _log.info(
            "queue.resume_fact_extraction_completed",
            agent_run_id=payload.agent_run_id,
        )
        return payload.agent_run_id
    except Exception as exc:  # noqa: BLE001 — sanitize and fail the run
        _log.warning(
            "queue.resume_fact_extraction_error",
            agent_run_id=payload.agent_run_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        fail_run(payload.agent_run_id, error="resume fact extraction failed")
        return "failed"


async def resume_aware_jd_analysis(
    ctx: dict[str, Any],
    payload: ResumeAwareJdAnalysisPayload | dict[str, Any],
) -> str:
    """Resume-aware JD analysis worker handler — executes the analysis workflow.

    Receives only durable resource IDs (``job_id``, ``resume_version_id``) in the
    payload — no raw JD or resume text crosses the queue boundary. The worker
    re-reads the ``JobPosting`` / ``ResumeVersion`` from its own DB session,
    re-loads the queued ``AgentRun`` by ID, re-checks user ownership, and
    delegates to
    :func:`app.services.jd_analysis_service.run_resume_aware_jd_analysis_worker`
    for the fixed-step orchestration (model call → validation → sanitized
    step/run persistence → ``JobAnalysis`` / ``GeneratedArtifact``).

    The model gateway is constructed inside the worker via
    :func:`app.models_gateway.factory.get_model_gateway` so no request-scoped
    dependency is passed across the queue boundary.

    On any uncaught exception the shared :func:`fail_run` guard flips the run
    to ``failed`` with a sanitized error so it never stays stuck on
    ``running``.
    """
    _ = ctx
    if isinstance(payload, dict):
        payload = ResumeAwareJdAnalysisPayload.model_validate(payload)

    # Construct the model gateway inside the worker process.
    from app.models_gateway.factory import get_model_gateway
    from app.services.jd_analysis_service import run_resume_aware_jd_analysis_worker

    gateway = get_model_gateway()

    try:
        with SessionLocal() as db:
            run = agent_run_repo.get_run(db, payload.agent_run_id)
            if run is None:
                _log.warning(
                    "queue.resume_aware_jd_analysis_missing_run",
                    agent_run_id=payload.agent_run_id,
                )
                return "missing_run"

            # Re-check ownership: a cross-user payload must not execute.
            if run.user_id != payload.user_id:
                _log.warning(
                    "queue.resume_aware_jd_analysis_owner_mismatch",
                    agent_run_id=payload.agent_run_id,
                    payload_user=payload.user_id,
                    run_user=run.user_id,
                )
                fail_run(payload.agent_run_id, error="ownership mismatch")
                return "ownership_mismatch"

        # The worker runner opens its own session; the ownership check above is
        # a fast-fail guard so a cross-user payload never reaches the service.
        await run_resume_aware_jd_analysis_worker(
            job_id=payload.job_id,
            resume_version_id=payload.resume_version_id,
            user_id=payload.user_id,
            agent_run_id=payload.agent_run_id,
            gateway=gateway,
        )
        _log.info(
            "queue.resume_aware_jd_analysis_completed",
            agent_run_id=payload.agent_run_id,
        )
        return payload.agent_run_id
    except Exception as exc:  # noqa: BLE001 — sanitize and fail the run
        _log.warning(
            "queue.resume_aware_jd_analysis_error",
            agent_run_id=payload.agent_run_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        fail_run(payload.agent_run_id, error="jd analysis failed")
        return "failed"
