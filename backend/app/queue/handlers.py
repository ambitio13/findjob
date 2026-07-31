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

This module currently contains only the smoke handler. Real workflow handlers
(jd_paste_parsing, resume_fact_extraction, resume_aware_jd_analysis) are added
by their respective migration tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.repositories import agent_run_repo
from app.db.session import SessionLocal
from app.queue.payloads import SmokePayload

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
