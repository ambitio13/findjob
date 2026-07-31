"""Profile draft apply service.

Maps structured resume facts (``ResumeVersion.parsed_facts.facts``) into
``UserProfileUpdate``-compatible fields and produces a preview diff before
writing. Non-empty existing profile fields are not overwritten unless
``overwrite=True``.

This service is user-scoped and version-scoped: the caller verifies resume /
version ownership before invoking :func:`apply_profile_draft`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import ResumeVersion, UserProfile
from app.db.repositories import user_profile_repo
from app.schemas.profile_draft import (
    ApplyProfileDraftRequest,
    ApplyProfileDraftResponse,
    ProfileDraftFieldDiff,
)
from app.schemas.user import UserProfileUpdate
from app.services.profile_service import _profile_to_read

_log = get_logger("app.services.profile_draft_service")


def _extract_facts(version: ResumeVersion) -> dict[str, Any]:
    """Return the typed ``facts`` dict from a resume version, or ``{}``."""
    parsed = version.parsed_facts or {}
    return parsed.get("facts") or {}


def _build_draft_updates(facts: dict[str, Any]) -> dict[str, Any]:
    """Map resume facts into profile update fields.

    Only fields the facts can reliably infer are mapped:
    - ``contact.name`` → ``display_name``
    - ``target_direction`` → ``career_direction``
    - ``locations[0]`` → ``base_location``
    - ``locations`` → ``preferred_locations``
    - ``strengths`` + ``highlights`` → ``strengths``

    Salary and constraints are left to the user / form.
    """
    updates: dict[str, Any] = {}

    contact = facts.get("contact") or {}
    name = contact.get("name") if isinstance(contact, dict) else None
    if name and isinstance(name, str) and name.strip():
        updates["display_name"] = name.strip()

    direction = facts.get("target_direction")
    if direction and isinstance(direction, str) and direction.strip():
        updates["career_direction"] = direction.strip()

    locations = facts.get("locations")
    if isinstance(locations, list) and locations:
        cleaned = [str(loc).strip() for loc in locations if str(loc).strip()]
        if cleaned:
            # First location becomes base_location; the full list becomes
            # preferred_locations (design.md: "first item of facts.locations").
            updates["base_location"] = cleaned[0]
            updates["preferred_locations"] = cleaned

    strengths_raw = facts.get("strengths") or []
    highlights = facts.get("highlights") or []
    combined: list[str] = []
    for src in (strengths_raw, highlights):
        if isinstance(src, list):
            for s in src:
                if isinstance(s, str) and s.strip():
                    combined.append(s.strip())
    if combined:
        # De-duplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for s in combined:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        updates["strengths"] = deduped

    return updates


def _current_value(user: UserProfile, field: str) -> Any:
    """Return the current value of a profile field, or ``None`` when empty."""
    val = getattr(user, field, None)
    if val is None:
        return None
    if isinstance(val, (list, dict)) and len(val) == 0:
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    return val


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def apply_profile_draft(
    db: Session,
    user: UserProfile,
    version: ResumeVersion,
    request: ApplyProfileDraftRequest,
) -> ApplyProfileDraftResponse:
    """Compute a preview diff and optionally apply resume facts to the profile.

    ``confirm=False`` → preview only (no write).
    ``confirm=True`` → write allowed changes.
    ``overwrite=False`` → skip fields whose current value is non-empty.
    ``overwrite=True`` → overwrite even non-empty current values.
    """
    facts = _extract_facts(version)
    draft = _build_draft_updates(facts)

    diffs: list[ProfileDraftFieldDiff] = []
    writable: dict[str, Any] = {}

    for field, draft_value in draft.items():
        current = _current_value(user, field)
        changed = _values_differ(current, draft_value)

        if not changed:
            diffs.append(
                ProfileDraftFieldDiff(
                    field=field,
                    current_value=current,
                    draft_value=draft_value,
                    will_change=False,
                )
            )
            continue

        blocked = False
        blocked_reason: str | None = None
        if not request.overwrite and not _is_empty(current):
            blocked = True
            blocked_reason = "当前值非空，需 overwrite=true 才会覆盖"

        diffs.append(
            ProfileDraftFieldDiff(
                field=field,
                current_value=current,
                draft_value=draft_value,
                will_change=not blocked,
                blocked_reason=blocked_reason,
            )
        )
        if not blocked:
            writable[field] = draft_value

    applied = False
    updated_profile_dict: dict[str, Any] | None = None

    if request.confirm and writable:
        _log.info(
            "profile_draft.apply",
            user_id=user.id,
            fields=list(writable.keys()),
            overwrite=request.overwrite,
        )
        # Build a UserProfileUpdate so the service-level merge / validation
        # path is reused (consistent with PATCH /users/me).
        payload = UserProfileUpdate(**writable)
        fields_to_write = payload.model_dump(exclude_unset=True)
        user_profile_repo.update_fields(db, user, fields_to_write)
        db.commit()
        fresh = user_profile_repo.get(db, user.id)
        if fresh is not None:
            updated_profile_dict = _profile_to_read(fresh).model_dump(mode="json")
        applied = True

    return ApplyProfileDraftResponse(
        applied=applied,
        confirm=request.confirm,
        overwrite=request.overwrite,
        diffs=diffs,
        updated_profile=updated_profile_dict,
    )


def _values_differ(current: Any, draft: Any) -> bool:
    """Compare current and draft values, normalizing list order for lists."""
    if _is_empty(current) and _is_empty(draft):
        return False
    if isinstance(current, list) and isinstance(draft, list):
        return sorted(str(x) for x in current) != sorted(str(x) for x in draft)
    return current != draft
