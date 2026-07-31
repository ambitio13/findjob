"""Repository for the ``JobAnalysis`` model.

Encapsulates persistence so route handlers and services stay free of raw query
code (per ``.trellis/spec/backend/database.md`` Repository Rules). The
``JobAnalysis`` row stores normalized scores and JSON note blobs; the full
validated structured output lives on the linked ``GeneratedArtifact``.
``JobAnalysis`` does not carry ``resume_version_id`` — resume provenance is
stored in ``GeneratedArtifact.source_ids`` (see design.md §4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import JobAnalysis


def create(
    db: Session,
    job_id: str,
    *,
    agent_run_id: str | None = None,
    match_score: float | None = None,
    risk_score: float | None = None,
    summary: str | None = None,
    salary_analysis: dict[str, Any] | None = None,
    growth_analysis: dict[str, Any] | None = None,
    stability_analysis: dict[str, Any] | None = None,
) -> JobAnalysis:
    """Insert a ``JobAnalysis`` row and return it (not yet committed)."""
    analysis = JobAnalysis(
        job_id=job_id,
        agent_run_id=agent_run_id,
        match_score=match_score,
        risk_score=risk_score,
        summary=summary,
        salary_analysis=salary_analysis,
        growth_analysis=growth_analysis,
        stability_analysis=stability_analysis,
    )
    db.add(analysis)
    db.flush()
    return analysis


def get(db: Session, analysis_id: str) -> JobAnalysis | None:
    """Return the ``JobAnalysis`` for ``analysis_id`` or ``None``."""
    return db.get(JobAnalysis, analysis_id)


def list_for_job(
    db: Session,
    job_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[JobAnalysis], int]:
    """Return ``(rows, total)`` of analyses for ``job_id``, newest first."""
    base_filter = JobAnalysis.job_id == job_id
    total = db.execute(
        select(func.count()).select_from(JobAnalysis).where(base_filter)
    ).scalar_one()
    rows = (
        db.execute(
            select(JobAnalysis)
            .where(base_filter)
            .order_by(JobAnalysis.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total
