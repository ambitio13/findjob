"""Users router: current-user profile and job-search preferences."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.models import UserProfile
from app.schemas.user import UserProfileRead, UserProfileUpdate
from app.services import profile_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserProfileRead)
def get_me(current_user: UserProfile = Depends(get_current_user)) -> UserProfileRead:
    """Return the current user's profile, creating the demo profile if needed."""
    # ``current_user`` is loaded via Depends; the service re-fetches to be safe.
    db = Session.object_session(current_user)
    return profile_service.read_profile(db, current_user)


@router.patch("/me", response_model=UserProfileRead)
def patch_me(
    payload: UserProfileUpdate,
    current_user: UserProfile = Depends(get_current_user),
) -> UserProfileRead:
    """Partially update the current user's profile.

    Fields omitted from the body are left untouched; fields sent as ``null``
    clear the underlying nullable column.
    """
    db = Session.object_session(current_user)
    return profile_service.update_profile(db, current_user, payload)
