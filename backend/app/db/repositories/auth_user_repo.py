"""Repository for the ``AuthUser`` model.

Only identity lookups live here; password verification and token issuance
are service-layer concerns (``app.services.auth_service``).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.models import AuthUser


def get(db: Session, user_id: str) -> AuthUser | None:
    return db.get(AuthUser, user_id)


def get_by_username(db: Session, username: str) -> AuthUser | None:
    return db.query(AuthUser).filter(AuthUser.username == username).one_or_none()


def create(
    db: Session, *, user_id: str, username: str, password_hash: str, display_name: str
) -> AuthUser:
    """Insert a new auth user with an explicit id (shared with UserProfile)."""
    row = AuthUser(
        id=user_id,
        username=username,
        password_hash=password_hash,
        display_name=display_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
