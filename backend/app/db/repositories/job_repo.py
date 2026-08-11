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
    source_url: str | None = None,
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
        source_url=source_url,
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


def update(db: Session, job: JobPosting, **fields: Any) -> JobPosting:
    """Apply a partial update to ``job`` in place (not yet committed).

    Only known editable columns are applied; unknown keys are ignored so a
    stray client field cannot clobber ``user_id`` / ``id`` / timestamps. The
    caller must have already resolved the owned ``job`` row (``get_by_id``)
    before invoking this — ownership is not re-checked here.

    Each value in ``fields`` is applied verbatim, including ``None``. Callers
    must distinguish "field not supplied" from "field explicitly cleared to
    ``None``" *before* calling this — once a key is in ``fields`` it is written.
    The PATCH endpoint uses ``JobUpdate.model_fields_set`` to pass only the
    keys the client actually sent, so an explicit ``null`` clears the column
    while an omitted key is left untouched.
    """
    editable = {
        "company",
        "title",
        "location",
        "salary_range",
        "direction",
        "platform",
        "external_id",
        "jd_raw",
        "jd_normalized",
        "source_url",
    }
    for key, value in fields.items():
        if key in editable:
            setattr(job, key, value)
    db.flush()
    return job


def find_by_platform_and_external_id(
    db: Session,
    *,
    user_id: str,
    platform: str,
    external_id: str,
) -> JobPosting | None:
    """Return the owned ``JobPosting`` matching ``platform`` + ``external_id``,
    or ``None``.

    Used by the BOSS recommended-job flow to dedup jobs read from the browser:
    ``external_id`` stores the sanitized ``page_url_hash`` so re-reading the same
    BOSS page returns the existing job instead of creating a duplicate.

    Scopes to ``user_id`` so cross-user access returns ``None`` (mapped to 404
    by the caller) instead of leaking existence.
    """
    return (
        db.execute(
            select(JobPosting)
            .where(
                JobPosting.user_id == user_id,
                JobPosting.platform == platform,
                JobPosting.external_id == external_id,
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


def list_for_user(
    db: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    id_filter: set[str] | None = None,
    exclude_ids: set[str] | None = None,
) -> tuple[list[JobPosting], int]:
    """Return ``(rows, total)`` of jobs owned by ``user_id``, newest first.

    ``id_filter`` restricts to the given ids (empty set ⇒ empty result, so
    callers can express "no matches" without a special case); ``exclude_ids``
    drops the given ids. Both power the job-list red-flag filter (Phase 3)
    while keeping pagination counts correct.
    """
    base_filter = JobPosting.user_id == user_id
    if id_filter is not None:
        if not id_filter:
            return [], 0
        base_filter = base_filter & JobPosting.id.in_(id_filter)
    if exclude_ids:
        base_filter = base_filter & JobPosting.id.not_in(exclude_ids)
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
