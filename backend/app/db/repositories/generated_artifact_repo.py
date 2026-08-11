"""Repository for the ``GeneratedArtifact`` model.

Encapsulates persistence so route handlers and services stay free of raw query
code (per ``.trellis/spec/backend/database.md`` Repository Rules). A
``GeneratedArtifact`` row stores the validated structured-output JSON in
``content`` plus generation provenance (source IDs, prompt version, model
name) so any later replay can explain what the model knew and produced.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import GeneratedArtifact


def create(
    db: Session,
    *,
    artifact_type: str,
    content: str,
    user_id: str | None = None,
    job_id: str | None = None,
    resume_version_id: str | None = None,
    agent_run_id: str | None = None,
    source_ids: dict[str, Any] | None = None,
    prompt_version: str | None = None,
    model_name: str | None = None,
) -> GeneratedArtifact:
    """Insert a ``GeneratedArtifact`` row and return it (not yet committed)."""
    artifact = GeneratedArtifact(
        artifact_type=artifact_type,
        content=content,
        user_id=user_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        agent_run_id=agent_run_id,
        source_ids=source_ids,
        prompt_version=prompt_version,
        model_name=model_name,
    )
    db.add(artifact)
    db.flush()
    return artifact


def get(db: Session, artifact_id: str) -> GeneratedArtifact | None:
    """Return the ``GeneratedArtifact`` for ``artifact_id`` or ``None``."""
    return db.get(GeneratedArtifact, artifact_id)


def get_latest_for_run(
    db: Session,
    run_id: str,
    *,
    artifact_type: str | None = None,
) -> GeneratedArtifact | None:
    """Return the newest ``GeneratedArtifact`` linked to ``run_id`` or ``None``.

    Used by read paths that need to reconstruct a result view from a persisted
    analysis row (e.g. re-parsing ``content`` into the structured output). The
    optional ``artifact_type`` filter keeps it scoped to one workflow's output.
    """
    base_filter = GeneratedArtifact.agent_run_id == run_id
    if artifact_type is not None:
        base_filter = base_filter & (GeneratedArtifact.artifact_type == artifact_type)
    return (
        db.execute(
            select(GeneratedArtifact)
            .where(base_filter)
            .order_by(GeneratedArtifact.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def list_for_application(
    db: Session,
    application_id: str,
    *,
    job_id: str,
    artifact_types: tuple[str, ...] | None = None,
    limit: int = 50,
) -> list[GeneratedArtifact]:
    """Return readiness artifacts for ``application_id``, newest first.

    ``GeneratedArtifact`` has no ``application_id`` FK column; the binding
    lives inside the ``source_ids`` JSON dict (key ``application_id``) written
    by the readiness worker. We scope by ``job_id`` (indexed FK) first, then
    filter in Python on ``source_ids['application_id']`` so the query stays
    cheap and dialect-neutral. ``artifact_types`` optionally restricts to the
    readiness types (excluding e.g. ``jd_analysis``).
    """
    candidate_limit = max(limit * 10, 200)
    base_filter = GeneratedArtifact.job_id == job_id
    if artifact_types is not None:
        base_filter = base_filter & (
            GeneratedArtifact.artifact_type.in_(artifact_types)
        )
    rows = (
        db.execute(
            select(GeneratedArtifact)
            .where(base_filter)
            .order_by(GeneratedArtifact.created_at.desc())
            .limit(candidate_limit)
        )
        .scalars()
        .all()
    )
    return [
        r
        for r in rows
        if (r.source_ids or {}).get("application_id") == application_id
    ][:limit]


def list_for_job(
    db: Session,
    job_id: str,
    *,
    artifact_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[GeneratedArtifact], int]:
    """Return ``(rows, total)`` of artifacts linked to ``job_id``, newest first.

    Optionally filter by ``artifact_type`` (e.g. ``"jd_analysis"``).
    """
    base_filter = GeneratedArtifact.job_id == job_id
    if artifact_type is not None:
        base_filter = base_filter & (GeneratedArtifact.artifact_type == artifact_type)
    total = db.execute(
        select(func.count()).select_from(GeneratedArtifact).where(base_filter)
    ).scalar_one()
    rows = (
        db.execute(
            select(GeneratedArtifact)
            .where(base_filter)
            .order_by(GeneratedArtifact.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total


def latest_opening_prompt_versions_by_job(
    db: Session,
    user_id: str,
    job_ids: list[str],
) -> dict[str, str | None]:
    """Map each ``job_id`` to its newest ``hr_opening_message`` prompt version.

    Powers the funnel's opening-prompt calibration bucket (Phase 1): reply
    rates grouped by prompt version answer "which opening prompt actually
    works". Jobs without a generated opening are simply absent from the map.
    """
    if not job_ids:
        return {}
    rows = (
        db.execute(
            select(GeneratedArtifact)
            .where(
                GeneratedArtifact.artifact_type == "hr_opening_message",
                GeneratedArtifact.user_id == user_id,
                GeneratedArtifact.job_id.in_(job_ids),
            )
            .order_by(GeneratedArtifact.created_at.asc())
        )
        .scalars()
        .all()
    )
    latest: dict[str, str | None] = {}
    for row in rows:  # ascending scan: later rows overwrite — newest wins
        if row.job_id is not None:
            latest[row.job_id] = row.prompt_version
    return latest
