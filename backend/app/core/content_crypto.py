"""Content-at-rest encryption for user-supplied long text.

Phase-review remediation: raw JD text (``job_postings.jd_raw``) must not be
persisted in plaintext. This module provides the envelope used by the
``EncryptedText`` column type:

- :func:`encrypt_text` — returns ``enc2$<fernet-token>`` for non-empty input
  (``enc1$`` for legacy rows that were encrypted under the old key).
- :func:`decrypt_text` — decrypts both ``enc1$`` and ``enc2$`` values; legacy
  plaintext rows are returned unchanged so pre-migration data and ORM-seeded
  test fixtures keep working.

Key versioning (08-15-content-key-decouple):

- ``enc1$`` — derived from ``AUTH_SECRET_KEY`` via SHA-256 (legacy, read-only).
  Kept so existing rows remain decryptable without a one-shot migration.
- ``enc2$`` — uses ``CONTENT_ENCRYPTION_KEY`` directly as the Fernet key.
  All new writes produce ``enc2$`` so content encryption is decoupled from
  token-signing key rotation.

In prod a missing ``CONTENT_ENCRYPTION_KEY`` is a hard configuration error
(fail-fast at first use); non-prod environments fall back to the ``enc1$``
derived-key path so local/test instances work without extra configuration.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("app.core.content_crypto")

#: Prefix marking a value encrypted under the legacy AUTH_SECRET_KEY-derived
#: key. Existing rows with this prefix are decrypted read-only; no new writes
#: produce ``enc1$``.
ENCRYPTED_PREFIX = "enc1$"

#: Prefix marking a value encrypted under the dedicated CONTENT_ENCRYPTION_KEY.
#: All new writes produce this prefix.
ENCRYPTED_PREFIX_V2 = "enc2$"

#: Non-prod fallback key material. Never used when ``app_env == "prod"``.
_DEV_FALLBACK_SECRET = "job-search-agent-dev-content-key-not-for-prod"


@lru_cache(maxsize=1)
def _fernet_v1() -> Fernet:
    """Legacy Fernet derived from AUTH_SECRET_KEY (read-only for enc1$ rows)."""
    settings = get_settings()
    secret = settings.auth_secret_key
    if not secret:
        if settings.app_env == "prod":
            raise RuntimeError(
                "AUTH_SECRET_KEY must be configured to decrypt legacy enc1$ content."
            )
        secret = _DEV_FALLBACK_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


@lru_cache(maxsize=1)
def _fernet_v2() -> Fernet:
    """Fernet for new enc2$ writes, using the dedicated content key.

    In prod ``CONTENT_ENCRYPTION_KEY`` is mandatory — a missing key is a
    hard startup error. In non-prod we fall back to the v1 derived key so
    local/test works without configuring a separate content key; a one-time
    warning is logged.
    """
    settings = get_settings()
    key = settings.content_encryption_key
    if not key:
        if settings.app_env == "prod":
            raise RuntimeError(
                "CONTENT_ENCRYPTION_KEY must be configured in prod. "
                "Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        _log.warning(
            "content_crypto.legacy_key_in_use",
            hint="Set CONTENT_ENCRYPTION_KEY to decouple content from AUTH_SECRET_KEY.",
        )
        return _fernet_v1()
    return Fernet(key.encode("ascii"))


def encrypt_text(plain: str) -> str:
    """Return the encrypted envelope for ``plain`` (empty strings pass through).

    New writes always produce ``enc2$``. Values already carrying ``enc1$`` or
    ``enc2$`` are returned unchanged (never double-wrap).
    """
    if not plain:
        return plain
    if plain.startswith(ENCRYPTED_PREFIX) or plain.startswith(ENCRYPTED_PREFIX_V2):
        return plain  # already encrypted — never double-wrap
    return ENCRYPTED_PREFIX_V2 + _fernet_v2().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    """Return the plaintext behind an ``enc1$`` or ``enc2$`` envelope.

    Legacy plaintext values (no prefix) are returned as-is. An envelope that
    fails to decrypt (e.g. rotated secret) collapses to an empty string and a
    logged error — ciphertext must never surface to callers as content.
    """
    if not value:
        return value
    if value.startswith(ENCRYPTED_PREFIX):
        fernet = _fernet_v1()
        token = value[len(ENCRYPTED_PREFIX):]
    elif value.startswith(ENCRYPTED_PREFIX_V2):
        fernet = _fernet_v2()
        token = value[len(ENCRYPTED_PREFIX_V2):]
    else:
        return value  # legacy plaintext
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        _log.error("content_crypto.decrypt_failed")
        return ""


__all__ = [
    "ENCRYPTED_PREFIX",
    "ENCRYPTED_PREFIX_V2",
    "decrypt_text",
    "encrypt_text",
]
