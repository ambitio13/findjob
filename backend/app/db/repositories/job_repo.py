"""Repository for the ``JobPosting`` model.

Encapsulates persistence so route handlers stay free of raw query code (per
``.trellis/spec/backend/database.md`` Repository Rules). This is a light
refactor to align the existing create/list/get paths with the Repository Rules
already followed by ``job_analysis_repo`` and ``resume_repo``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import JobPosting


def create(
    db: Session,
    *,
    user_id: str,
    company: str,
    title: str,
    jd_raw: str,
    platform: str = "manual",
    location: str | None = None,
    salary_range: str | None = None,
    direction: str | None = None,
    jd_normalized: dict[str, Any] | None = None,
) -> JobPosting:
    """Insert a ``JobPosting`` row and return it (not yet committed)."""
    job = JobPosting(
        user_id=user_id,
        platform=platform,
        company=company,
        title=title,
        location=location,
        salary_range=salary_range,
        direction=direction,
        jd_raw=jd_raw,
        jd_normalized=jd_normalized,
    )
    db.add(job)
    db.flush()
    return job


def get_by_id(db: Session, job_id: str, user_id: str) -> JobPosting | None:
    """Return the ``JobPosting`` for ``job_id`` owned by ``user_id``, or ``None``.

    Scopes to ``user_id`` so cross-user access returns ``None`` (mapped to 404
    by the caller) instead of leaking existence.
    """
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != user_id:
        return None
    return job


def list_for_user(
    db: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[JobPosting], int]:
    """Return ``(rows, total)`` of jobs owned by ``user_id``, newest first."""
    base_filter = JobPosting.user_id == user_id
    total = db.execute(select(func.count()).select_from(JobPosting).where(base_filter)).scalar_one()
    rows = (
        db.execute(
            select(JobPosting)
            .where(base_filter)
            .order_by(JobPosting.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total
