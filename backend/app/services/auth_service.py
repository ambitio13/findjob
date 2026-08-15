"""Authentication domain service: registration and login.

Owns the business rules around identity:

- invite-code gating (when ``AUTH_INVITE_CODE`` is configured);
- password hashing via :mod:`app.core.security`;
- token issuance bound to the auth user id;
- username-dimension login lockout (brute-force defense, E2).

The auth user id is reused as the ``UserProfile.id`` so every downstream
ownership check (jobs, resumes, applications, outcomes) works unchanged.

Security notes:

- Login failures return one identical error for unknown-user and
  wrong-password so the endpoint cannot enumerate usernames by timing or
  message differences (a dummy verify still runs to equalize cost).
- Account lockout returns the *same* ``invalid_credentials`` error — a locked
  account is indistinguishable from a wrong password to the caller.
- Passwords and password hashes are never logged.
"""

from __future__ import annotations

import hmac
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.db.repositories import auth_user_repo, user_profile_repo
from app.schemas.auth import USERNAME_PATTERN, AuthUserOut

_log = get_logger("app.services.auth_service")

#: Dummy hash verified on unknown usernames so both failure paths cost the
#: same PBKDF2 work (timing equalization). Computed once at import.
_DUMMY_HASH = hash_password("timing-equalization-dummy")

#: Pre-compiled username pattern for the lockout guard. The login schema
#: allows arbitrary bytes (only min/max length), so the lockout path must
#: reject values that don't match the registration pattern before they reach
#: Redis — this blocks arbitrary bytes from entering Redis keys and prevents
#: an attacker from creating unlimited distinct keys.
_USERNAME_RE = re.compile(USERNAME_PATTERN)

#: TTL for lockout-related keys — aligns with the lockout window so counters
#: never accumulate indefinitely.
_LOCKOUT_TTL_SECONDS = 900  # 15 minutes


def _lock_key(username: str) -> str:
    return f"lk:{get_settings().queue_namespace}:lock:{username}"


def _fail_key(username: str) -> str:
    return f"lk:{get_settings().queue_namespace}:fail:{username}"


def _is_locked(username: str, *, redis_client: Any = None) -> bool:
    """True when the account is currently locked. Fail-open on Redis errors."""
    try:
        client = redis_client
        if client is None:
            from app.cache.redis import get_redis_client

            client = get_redis_client().client
        return bool(client.exists(_lock_key(username)))
    except Exception:
        _log.warning("auth.lockout_redis_error", action="is_locked_fail_open")
        return False


def _record_failure(username: str, *, redis_client: Any = None) -> None:
    """Increment the failure counter; set the lock key when the threshold is hit."""
    settings = get_settings()
    try:
        client = redis_client
        if client is None:
            from app.cache.redis import get_redis_client

            client = get_redis_client().client
        key = _fail_key(username)
        count = client.incr(key)
        if count == 1:
            client.expire(key, _LOCKOUT_TTL_SECONDS)
        if count >= settings.auth_login_max_failures:
            client.set(_lock_key(username), "1", ex=_LOCKOUT_TTL_SECONDS)
    except Exception:
        _log.warning("auth.lockout_redis_error", action="record_failure_fail_open")


def _clear_failures(username: str, *, redis_client: Any = None) -> None:
    """Delete the failure counter and lock on successful login."""
    try:
        client = redis_client
        if client is None:
            from app.cache.redis import get_redis_client

            client = get_redis_client().client
        client.delete(_fail_key(username), _lock_key(username))
    except Exception:
        _log.warning("auth.lockout_redis_error", action="clear_failures_fail_open")


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
    failure — wrong password, unknown user, or locked account. Requires
    ``AUTH_SECRET_KEY`` to be configured.

    Account lockout (E2): after ``auth_login_max_failures`` consecutive
    failures the username is locked for ``auth_lockout_minutes``. A locked
    account returns the same ``invalid_credentials`` error so the lock is
    not enumerable. Successful login clears the failure counter. Redis
    failures are fail-open (login proceeds) to match the rate-limiter
    strategy.

    Note: usernames that don't match the registration pattern skip the
    lockout path entirely — the login schema allows arbitrary bytes, but
    only pattern-valid usernames can be real accounts (enforced at
    registration). This prevents arbitrary bytes from entering Redis keys.
    The locked branch raises immediately without PBKDF2; the timing
    difference is acceptable because a locked state itself proves ongoing
    brute-force, adding no new enumeration signal.
    """
    settings = get_settings()
    if not settings.auth_secret_key:
        raise AuthError("auth_not_configured")

    # Only pattern-valid usernames participate in lockout — blocks arbitrary
    # bytes from entering Redis keys. Non-matching usernames fall through to
    # normal verification (which will always fail for unknown users).
    if _USERNAME_RE.match(username):
        if _is_locked(username):
            raise AuthError("invalid_credentials")

    row = auth_user_repo.get_by_username(db, username)
    stored_hash = row.password_hash if row is not None else _DUMMY_HASH
    if not verify_password(password, stored_hash) or row is None:
        if _USERNAME_RE.match(username):
            _record_failure(username)
        _log.warning("auth.login_failed", username=username)
        raise AuthError("invalid_credentials")

    if _USERNAME_RE.match(username):
        _clear_failures(username)

    token = create_access_token(row.id)
    _log.info("auth.login_succeeded", user_id=row.id)
    return AuthUserOut(id=row.id, username=row.username, display_name=row.display_name), token
