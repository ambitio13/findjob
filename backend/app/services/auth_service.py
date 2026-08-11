"""Authentication domain service: registration and login.

Owns the business rules around identity:

- invite-code gating (when ``AUTH_INVITE_CODE`` is configured);
- password hashing via :mod:`app.core.security`;
- token issuance bound to the auth user id.

The auth user id is reused as the ``UserProfile.id`` so every downstream
ownership check (jobs, resumes, applications, outcomes) works unchanged.

Security notes:

- Login failures return one identical error for unknown-user and
  wrong-password so the endpoint cannot enumerate usernames by timing or
  message differences (a dummy verify still runs to equalize cost).
- Passwords and password hashes are never logged.
"""

from __future__ import annotations

import hmac
import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.db.repositories import auth_user_repo, user_profile_repo
from app.schemas.auth import AuthUserOut

_log = get_logger("app.services.auth_service")

#: Dummy hash verified on unknown usernames so both failure paths cost the
#: same PBKDF2 work (timing equalization). Computed once at import.
_DUMMY_HASH = hash_password("timing-equalization-dummy")


class AuthError(Exception):
    """Raised for any failed registration/login. Carries a stable reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def register(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str | None,
    invite_code: str | None,
) -> AuthUserOut:
    """Create a new auth user + profile row and return the identity.

    Raises :class:`AuthError` with reason ``invite_code_required`` /
    ``invite_code_invalid`` / ``username_taken``.
    """
    settings = get_settings()
    if settings.auth_invite_code:
        if not invite_code:
            raise AuthError("invite_code_required")
        if not hmac.compare_digest(invite_code, settings.auth_invite_code):
            _log.warning("auth.register_invite_invalid", username=username)
            raise AuthError("invite_code_invalid")

    if auth_user_repo.get_by_username(db, username) is not None:
        raise AuthError("username_taken")

    user_id = uuid.uuid4().hex
    password_hash = hash_password(password)
    row = auth_user_repo.create(
        db,
        user_id=user_id,
        username=username,
        password_hash=password_hash,
        display_name=display_name or username,
    )
    # Establish the profile row with the SAME id so ownership checks work.
    profile = user_profile_repo.ensure_default(db, user_id)
    profile.display_name = display_name or username
    db.commit()

    _log.info("auth.registered", user_id=user_id)
    return AuthUserOut(id=row.id, username=row.username, display_name=row.display_name)


def login(db: Session, *, username: str, password: str) -> tuple[AuthUserOut, str]:
    """Verify credentials and return ``(identity, access_token)``.

    Raises :class:`AuthError` with reason ``invalid_credentials`` on any
    failure. Requires ``AUTH_SECRET_KEY`` to be configured.
    """
    settings = get_settings()
    if not settings.auth_secret_key:
        raise AuthError("auth_not_configured")

    row = auth_user_repo.get_by_username(db, username)
    stored_hash = row.password_hash if row is not None else _DUMMY_HASH
    if not verify_password(password, stored_hash) or row is None:
        _log.warning("auth.login_failed", username=username)
        raise AuthError("invalid_credentials")

    token = create_access_token(row.id)
    _log.info("auth.login_succeeded", user_id=row.id)
    return AuthUserOut(id=row.id, username=row.username, display_name=row.display_name), token
