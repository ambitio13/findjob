"""Content-at-rest encryption for user-supplied long text.

Phase-review remediation: raw JD text (``job_postings.jd_raw``) must not be
persisted in plaintext. This module provides the envelope used by the
``EncryptedText`` column type:

- :func:`encrypt_text` — returns ``enc1$<fernet-token>`` for non-empty input.
- :func:`decrypt_text` — decrypts ``enc1$`` values; legacy plaintext rows are
  returned unchanged so pre-migration data and ORM-seeded test fixtures keep
  working until the cleanup migration has run everywhere.

The Fernet key is derived deterministically from ``AUTH_SECRET_KEY``
(SHA-256 → urlsafe base64), so every process that can already sign access
tokens can decrypt content, and no extra key management is introduced. In
prod a missing ``AUTH_SECRET_KEY`` is a hard configuration error; non-prod
environments fall back to a fixed development key so local/test instances
work without extra configuration (token issuance is likewise disabled there).
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("app.core.content_crypto")

#: Prefix marking an encrypted value. Rows without it are legacy plaintext.
ENCRYPTED_PREFIX = "enc1$"

#: Non-prod fallback key material. Never used when ``app_env == "prod"``.
_DEV_FALLBACK_SECRET = "job-search-agent-dev-content-key-not-for-prod"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.auth_secret_key
    if not secret:
        if settings.app_env == "prod":
            raise RuntimeError(
                "AUTH_SECRET_KEY must be configured to encrypt content at rest."
            )
        secret = _DEV_FALLBACK_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_text(plain: str) -> str:
    """Return the encrypted envelope for ``plain`` (empty strings pass through)."""
    if not plain:
        return plain
    if plain.startswith(ENCRYPTED_PREFIX):
        return plain  # already encrypted — never double-wrap
    return ENCRYPTED_PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    """Return the plaintext behind an ``enc1$`` envelope.

    Legacy plaintext values (no prefix) are returned as-is. An envelope that
    fails to decrypt (e.g. rotated secret) collapses to an empty string and a
    logged error — ciphertext must never surface to callers as content.
    """
    if not value or not value.startswith(ENCRYPTED_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(ENCRYPTED_PREFIX):].encode("ascii")).decode(
            "utf-8"
        )
    except (InvalidToken, ValueError):
        _log.error("content_crypto.decrypt_failed")
        return ""


__all__ = ["ENCRYPTED_PREFIX", "decrypt_text", "encrypt_text"]
