"""Profile service.

Owns the business behavior of reading and updating the current user's profile.
Route handlers delegate here; this service calls the repository and maps the
ORM row to the outbound schema. No transport concerns leak into this layer.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.db.repositories import user_profile_repo
from app.schemas.user import UserProfileRead, UserProfileUpdate

_log = get_logger("app.services.profile_service")


def read_profile(db: Session, user: UserProfile) -> UserProfileRead:
    """Return the current user's profile as an outbound schema."""
    # ``user`` is already loaded (and ensured) by the ``get_current_user``
    # dependency, but we re-fetch to avoid returning a stale reference after
    # an update in the same request.
    fresh = user_profile_repo.get(db, user.id)
    if fresh is None:
        # Defensive: ensure_default should have created the row; this branch
        # should not be reachable in normal flow.
        raise RuntimeError(f"user profile vanished for {user.id}")
    return UserProfileRead.model_validate(fresh)


def update_profile(db: Session, user: UserProfile, payload: UserProfileUpdate) -> UserProfileRead:
    """Apply a partial update to the current user's profile.

    Uses ``model_dump(exclude_unset=True)`` so fields omitted from the request
    are left untouched, while fields explicitly sent as ``null`` clear the
    underlying nullable column.
    """
    fields = payload.model_dump(exclude_unset=True)
    # Log field names + lengths only; never log full payloads (may contain
    # sensitive career context).
    _log.info(
        "user_profile.update",
        user_id=user.id,
        fields=list(fields.keys()),
        preferred_locations_len=len(fields.get("preferred_locations") or []) or None,
        strengths_len=len(fields.get("strengths") or []) or None,
    )
    updated = user_profile_repo.update_fields(db, user, fields)
    db.commit()
    return UserProfileRead.model_validate(updated)
