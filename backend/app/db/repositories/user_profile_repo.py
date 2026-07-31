"""Repository for the ``UserProfile`` model.

Encapsulates persistence concerns so route handlers and services stay free of
raw query code (per ``.trellis/spec/backend/database.md`` Repository Rules).
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import UserProfile

_log = get_logger("app.db.repositories.user_profile_repo")

_DEFAULT_DISPLAY_NAME = "演示用户"


def get(db: Session, user_id: str) -> UserProfile | None:
    """Return the profile for ``user_id`` or ``None`` if it does not exist."""
    return db.get(UserProfile, user_id)


def ensure_default(db: Session, user_id: str) -> UserProfile:
    """Ensure a profile row exists for ``user_id``; return it.

    Inserts a row with ``display_name="演示用户"`` if absent. Does NOT update
    existing rows — read requests must not touch ``updated_at``. Safe under
    concurrent inserts via the primary-key unique constraint.

    Uses PostgreSQL ``ON CONFLICT DO NOTHING`` when available; falls back to a
    get-then-create path for SQLite/test backends.
    """
    existing = get(db, user_id)
    if existing is not None:
        # Read path: do NOT commit or touch the row. GET requests must never
        # bump ``updated_at``.
        return existing

    _log.info("user_profile.ensure_default", user_id=user_id)
    dialect_name = db.bind.dialect.name if db.bind else ""

    if dialect_name == "postgresql":
        stmt = (
            pg_insert(UserProfile)
            .values(id=user_id, display_name=_DEFAULT_DISPLAY_NAME)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        db.execute(stmt)
        db.commit()
    elif dialect_name == "sqlite":
        stmt = (
            sqlite_insert(UserProfile)
            .values(id=user_id, display_name=_DEFAULT_DISPLAY_NAME)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        db.execute(stmt)
        db.commit()
    else:
        # Generic fallback: try to insert; ignore unique violation races.
        try:
            db.add(UserProfile(id=user_id, display_name=_DEFAULT_DISPLAY_NAME))
            db.commit()
        except Exception:
            db.rollback()

    # Re-fetch in a stable state (the row now durably exists). ``expire_on_commit``
    # is False on SessionLocal, so this SELECT returns the persisted timestamps
    # rather than a transient in-memory copy.
    row = get(db, user_id)
    # At this point the row must exist; if not, the dialect fallback failed in
    # a way we cannot recover from — let the error surface.
    assert row is not None, f"ensure_default failed to establish row for {user_id}"
    return row


def update_fields(db: Session, user: UserProfile, fields: dict[str, object]) -> UserProfile:
    """Apply ``fields`` to ``user``, flush, and return the refreshed row."""
    for key, value in fields.items():
        setattr(user, key, value)
    db.flush()
    db.refresh(user)
    return user
