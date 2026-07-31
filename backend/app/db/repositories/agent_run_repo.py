"""Repository for ``AgentRun`` and ``AgentStep`` models.

Encapsulates persistence so route handlers and services stay free of raw query
code (per ``.trellis/spec/backend/database.md`` Repository Rules). A run owns
its steps via cascade; this module offers create/update helpers that keep the
two tables consistent without leaking ORM mechanics into callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import AgentRun, AgentStep


def create_run(
    db: Session,
    *,
    user_id: str | None,
    workflow_type: str,
    status: str = "queued",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> AgentRun:
    """Insert an ``AgentRun`` row and return it (not yet committed)."""
    run = AgentRun(
        user_id=user_id,
        workflow_type=workflow_type,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
        result=result,
    )
    db.add(run)
    db.flush()
    return run


def update_status(
    db: Session,
    run: AgentRun,
    *,
    status: str,
    finished_at: datetime | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> AgentRun:
    """Apply a status transition to ``run``, flush, and return the refreshed row.

    Only non-``None`` keyword arguments are applied, so callers can update
    status without clobbering an existing ``result``.
    """
    run.status = status
    if finished_at is not None:
        run.finished_at = finished_at
    if error is not None:
        run.error = error
    if result is not None:
        run.result = result
    db.flush()
    return run


def add_step(
    db: Session,
    *,
    run_id: str,
    step_no: int,
    name: str,
    status: str = "planned",
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> AgentStep:
    """Insert an ``AgentStep`` row for ``run_id`` (not yet committed)."""
    step = AgentStep(
        run_id=run_id,
        step_no=step_no,
        name=name,
        status=status,
        result=result,
        error=error,
    )
    db.add(step)
    db.flush()
    return step


def get_run(db: Session, run_id: str) -> AgentRun | None:
    """Return the ``AgentRun`` for ``run_id`` or ``None``."""
    return db.get(AgentRun, run_id)


def list_runs_for_user(
    db: Session,
    user_id: str,
    *,
    workflow_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AgentRun], int]:
    """Return ``(rows, total)`` of runs for ``user_id``, newest first.

    Optionally filter by ``workflow_type`` (e.g.
    ``"resume_aware_jd_analysis"``).
    """
    base_filter = AgentRun.user_id == user_id
    if workflow_type is not None:
        base_filter = base_filter & (AgentRun.workflow_type == workflow_type)
    total = db.execute(select(func.count()).select_from(AgentRun).where(base_filter)).scalar_one()
    rows = (
        db.execute(
            select(AgentRun)
            .where(base_filter)
            .order_by(AgentRun.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total


def list_steps(db: Session, run_id: str) -> list[AgentStep]:
    """Return all steps for ``run_id`` ordered by ``step_no``."""
    return (
        db.execute(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.step_no.asc())
        )
        .scalars()
        .all()
    )
