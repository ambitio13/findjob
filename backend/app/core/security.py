"""Security primitives: password hashing and signed access tokens.

Deliberately dependency-free (stdlib only):

- Passwords are hashed with PBKDF2-HMAC-SHA256 and a per-user random salt.
- Access tokens are ``base64url(payload).base64url(signature)`` envelopes
  signed with HMAC-SHA256 over the exact payload bytes. They carry ``sub``
  (subject = auth user id), ``iat``, and ``exp`` claims only.

Both verification paths use :func:`hmac.compare_digest` for constant-time
comparison. Tokens never carry secrets, cookies, or platform session data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("app.core.security")

_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 390_000
_SALT_BYTES = 16
_TOKEN_LEEWAY_SECONDS = 30


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a PBKDF2-HMAC-SHA256 hash string for ``password``.

    Format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``. The format
    prefix makes future algorithm migration detectable at verify time.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Return ``True`` when ``password`` matches ``stored_hash``.

    Malformed or unknown-format hashes always return ``False`` (never raise),
    so a bad row cannot be used to probe the auth endpoint.
    """
    try:
        algo, iterations_raw, salt_hex, expected_hex = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        iterations = int(iterations_raw)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


# ---------------------------------------------------------------------------
# Signed access tokens
# ---------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload_bytes: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()


def create_access_token(subject: str, *, ttl_minutes: int | None = None) -> str:
    """Create a signed access token for ``subject`` (an auth user id).

    The token only carries ``sub``, ``iat``, and ``exp``. Anything richer
    (roles, scopes) belongs in the database, never in a bearer token.
    """
    settings = get_settings()
    if not settings.auth_secret_key:
        raise RuntimeError("AUTH_SECRET_KEY must be configured to issue access tokens.")
    ttl = ttl_minutes if ttl_minutes is not None else settings.auth_token_ttl_minutes
    now = int(time.time())
    payload = {"sub": subject, "iat": now, "exp": now + ttl * 60}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = _sign(payload_bytes, settings.auth_secret_key)
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> str | None:
    """Return the subject when ``token`` is valid and unexpired, else ``None``.

    All failure modes (bad shape, bad signature, expired, bad secret config)
    collapse to ``None`` so callers never distinguish *why* a token failed —
    the response is always an identical 401.
    """
    settings = get_settings()
    if not settings.auth_secret_key:
        return None
    try:
        payload_part, signature_part = token.split(".")
        payload_bytes = _b64url_decode(payload_part)
        signature = _b64url_decode(signature_part)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(payload_bytes, settings.auth_secret_key), signature):
        _log.warning("auth.token_bad_signature")
        return None
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
        subject = payload["sub"]
        exp = int(payload["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(subject, str) or not subject:
        return None
    if time.time() > exp + _TOKEN_LEEWAY_SECONDS:
        return None
    return subject
