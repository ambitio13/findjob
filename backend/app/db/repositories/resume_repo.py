"""Repository for ``Resume`` and ``ResumeVersion`` models.

Encapsulates persistence so route handlers stay free of raw query code (per
``.trellis/spec/backend/database.md`` Repository Rules). Each upload creates a
fresh ``Resume`` row plus a ``ResumeVersion`` with ``version_no = 1``; the
versioning machinery is in place for a future re-parse endpoint, but this task
does not add one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.models import Resume, ResumeVersion


def create(
    db: Session,
    user_id: str,
    filename: str,
    storage_uri: str | None,
    mime_type: str | None,
) -> Resume:
    """Insert a ``Resume`` row and return it (not yet committed)."""
    resume = Resume(
        user_id=user_id,
        filename=filename,
        storage_uri=storage_uri,
        mime_type=mime_type,
    )
    db.add(resume)
    db.flush()
    return resume


def get(db: Session, resume_id: str) -> Resume | None:
    """Return the ``Resume`` for ``resume_id`` or ``None``."""
    return db.get(Resume, resume_id)


def list_for_user(
    db: Session,
    user_id: str,
    page: int,
    page_size: int,
) -> tuple[list[Resume], int]:
    """Return ``(rows, total)`` for resumes owned by ``user_id``."""
    base_filter = Resume.user_id == user_id
    total = db.execute(select(func.count()).select_from(Resume).where(base_filter)).scalar_one()
    rows = (
        db.execute(
            select(Resume)
            .where(base_filter)
            .order_by(Resume.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return rows, total


def create_version(
    db: Session,
    resume_id: str,
    raw_text: str,
    parsed_facts: dict[str, Any] | None,
) -> ResumeVersion:
    """Insert a ``ResumeVersion`` with the next ``version_no`` (1-based)."""
    max_no = db.execute(
        select(func.max(ResumeVersion.version_no)).where(ResumeVersion.resume_id == resume_id)
    ).scalar_one()
    version_no = (max_no or 0) + 1
    version = ResumeVersion(
        resume_id=resume_id,
        version_no=version_no,
        raw_text=raw_text,
        parsed_facts=parsed_facts,
    )
    db.add(version)
    db.flush()
    return version


def list_versions(db: Session, resume_id: str) -> list[ResumeVersion]:
    """Return all versions of a resume, newest first."""
    return (
        db.execute(
            select(ResumeVersion)
            .where(ResumeVersion.resume_id == resume_id)
            .order_by(ResumeVersion.version_no.desc())
        )
        .scalars()
        .all()
    )


def latest_version(db: Session, resume_id: str) -> ResumeVersion | None:
    """Return the highest-``version_no`` version, or ``None``."""
    return (
        db.execute(
            select(ResumeVersion)
            .where(ResumeVersion.resume_id == resume_id)
            .order_by(ResumeVersion.version_no.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
