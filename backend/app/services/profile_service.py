"""Profile service.

Owns the business behavior of reading and updating the current user's profile.
Route handlers delegate here; this service calls the repository and maps the
ORM row to the outbound schema. No transport concerns leak into this layer.

The ``constraints`` JSON column stores both named v1 fields and legacy keys.
On read, the service splits raw ``constraints`` into ``named_constraints``
(typed) and ``legacy_constraints`` (unknown keys, read-only). On update, the
service merges named fields into the existing ``constraints`` dict so legacy
keys are never lost.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.db.repositories import user_profile_repo
from app.schemas.user import (
    NAMED_CONSTRAINT_KEYS,
    ProfileConstraints,
    UserProfileRead,
    UserProfileUpdate,
)

_log = get_logger("app.services.profile_service")


def _split_constraints(
    raw: dict[str, Any] | None,
) -> tuple[ProfileConstraints, dict[str, Any] | None]:
    """Split raw ``constraints`` into typed named fields and legacy keys.

    Named keys are projected into ``ProfileConstraints``. Unknown keys are
    returned as a separate dict (``None`` when empty) so the UI can show them
    read-only.
    """
    if not raw:
        return ProfileConstraints(), None

    named_data = {k: raw[k] for k in NAMED_CONSTRAINT_KEYS if k in raw}
    legacy = {k: v for k, v in raw.items() if k not in NAMED_CONSTRAINT_KEYS}
    return (
        ProfileConstraints(**named_data),
        legacy if legacy else None,
    )


def _profile_to_read(profile: UserProfile) -> UserProfileRead:
    """Map an ORM ``UserProfile`` row to the outbound ``UserProfileRead``."""
    named, legacy = _split_constraints(profile.constraints)
    return UserProfileRead(
        id=profile.id,
        display_name=profile.display_name,
        email=profile.email,
        career_direction=profile.career_direction,
        base_location=profile.base_location,
        preferred_locations=profile.preferred_locations,
        salary_min=profile.salary_min,
        salary_max=profile.salary_max,
        strengths=profile.strengths,
        named_constraints=named,
        legacy_constraints=legacy,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


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
    return _profile_to_read(fresh)


def update_profile(db: Session, user: UserProfile, payload: UserProfileUpdate) -> UserProfileRead:
    """Apply a partial update to the current user's profile.

    Uses ``model_dump(exclude_unset=True)`` so fields omitted from the request
    are left untouched, while fields explicitly sent as ``null`` clear the
    underlying nullable column.

    Named constraint fields are merged into the existing ``constraints`` JSON
    column rather than replacing it wholesale, so legacy keys survive.
    """
    fields = payload.model_dump(exclude_unset=True)
    named_constraints = fields.pop("named_constraints", None)

    # Merge named constraints into the existing ``constraints`` dict.
    if named_constraints is not None:
        _merge_named_constraints(fields, user, named_constraints)

    # Log field names + lengths only; never log full payloads (may contain
    # sensitive career context).
    _log.info(
        "user_profile.update",
        user_id=user.id,
        fields=list(fields.keys()),
        preferred_locations_len=len(fields.get("preferred_locations") or []) or None,
        strengths_len=len(fields.get("strengths") or []) or None,
        constraint_keys=(
            [k for k, v in named_constraints.items() if v is not None]
            if named_constraints
            else None
        ),
    )
    updated = user_profile_repo.update_fields(db, user, fields)
    db.commit()
    return _profile_to_read(updated)


def _merge_named_constraints(
    fields: dict[str, Any],
    user: UserProfile,
    named_constraints: dict[str, Any],
) -> None:
    """Merge ``named_constraints`` into ``fields['constraints']``.

    Preserves legacy keys already present on ``user.constraints``. A named
    field sent as ``None`` clears that key from the dict (matching PATCH
    ``null``-clears semantics).
    """
    current = dict(user.constraints or {})
    for key in NAMED_CONSTRAINT_KEYS:
        if key not in named_constraints:
            continue
        value = named_constraints[key]
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    fields["constraints"] = current if current else None
