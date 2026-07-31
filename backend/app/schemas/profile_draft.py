"""Schemas for the resume → profile draft apply flow.

The resume fact extraction produces structured ``ResumeFacts``. The user can
preview and then apply a subset of those facts to their ``UserProfile`` via
``POST /resumes/{resume_id}/versions/{version_id}/apply-profile-draft``.

Default mode (``confirm=False``) returns a preview diff without writing.
Confirm mode (``confirm=True``) writes allowed changes through the same
repository path used by ``PATCH /users/me``. Non-empty existing profile
fields are not overwritten unless ``overwrite=True``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApplyProfileDraftRequest(BaseModel):
    """Request body for the apply-profile-draft endpoint."""

    confirm: bool = False
    overwrite: bool = False


class ProfileDraftFieldDiff(BaseModel):
    """A single field diff in the preview.

    ``current_value`` is the existing profile value (``None`` when empty).
    ``draft_value`` is what the resume facts suggest. ``will_change`` is
    ``True`` only when the draft differs from the current value AND the change
    would actually be written (i.e. not blocked by a non-empty current value
    when ``overwrite=False``).
    """

    field: str
    current_value: Any | None = None
    draft_value: Any | None = None
    will_change: bool = False
    blocked_reason: str | None = None


class ApplyProfileDraftResponse(BaseModel):
    """Response for the apply-profile-draft endpoint.

    ``applied=False`` for preview mode (``confirm=False``) or when all changes
    are blocked. ``applied=True`` after a successful confirm write.
    """

    applied: bool = False
    confirm: bool = False
    overwrite: bool = False
    diffs: list[ProfileDraftFieldDiff] = []
    updated_profile: dict[str, Any] | None = None
