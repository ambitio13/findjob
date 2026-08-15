"""Content-at-rest encryption tests (jd_raw privacy remediation).

Covers the envelope helpers and the end-to-end contract: raw JD text sent
through the public API is stored as ciphertext in ``job_postings.jd_raw``
while API responses and pipeline contexts still see the plaintext
(transparent ORM-level encryption).

Key versioning (08-15-content-key-decouple):

- New writes produce ``enc2$`` (dedicated CONTENT_ENCRYPTION_KEY).
- Legacy ``enc1$`` rows (AUTH_SECRET_KEY-derived) are still decryptable.
- Both prefixes coexist transparently.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.content_crypto import (
    ENCRYPTED_PREFIX,
    ENCRYPTED_PREFIX_V2,
    decrypt_text,
    encrypt_text,
)
from app.db.session import SessionLocal

_PLAIN_JD = "负责后端服务设计与开发，要求 3 年以上 Python 经验，薪资 20k-30k/月。"


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip() -> None:
    token = encrypt_text(_PLAIN_JD)
    assert token.startswith(ENCRYPTED_PREFIX_V2)
    assert _PLAIN_JD not in token
    assert decrypt_text(token) == _PLAIN_JD


def test_encrypt_is_not_deterministic() -> None:
    """Fernet carries a timestamp/IV: two encryptions must differ."""
    assert encrypt_text(_PLAIN_JD) != encrypt_text(_PLAIN_JD)


def test_encrypt_never_double_wraps() -> None:
    token = encrypt_text(_PLAIN_JD)
    assert encrypt_text(token) == token


def test_encrypt_never_double_wraps_v1() -> None:
    """An existing enc1$ value must also not be double-wrapped."""
    # Build a v1 token manually to simulate a legacy row.
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from app.core.config import get_settings

    settings = get_settings()
    secret = settings.auth_secret_key or "job-search-agent-dev-content-key-not-for-prod"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    fernet_v1 = Fernet(key)
    v1_token = ENCRYPTED_PREFIX + fernet_v1.encrypt(_PLAIN_JD.encode("utf-8")).decode("ascii")
    assert encrypt_text(v1_token) == v1_token  # not re-encrypted to enc2$


def test_empty_values_pass_through() -> None:
    assert encrypt_text("") == ""
    assert decrypt_text("") == ""


def test_legacy_plaintext_passes_through() -> None:
    """Rows written before the migration still decrypt to themselves."""
    assert decrypt_text(_PLAIN_JD) == _PLAIN_JD


def test_corrupted_envelope_v2_collapses_to_empty() -> None:
    assert decrypt_text(ENCRYPTED_PREFIX_V2 + "not-a-valid-token") == ""


def test_corrupted_envelope_v1_collapses_to_empty() -> None:
    assert decrypt_text(ENCRYPTED_PREFIX + "not-a-valid-token") == ""


# ---------------------------------------------------------------------------
# Dual-key coexistence (AC1 + AC2)
# ---------------------------------------------------------------------------


def test_enc1_legacy_row_decryptable() -> None:
    """A legacy enc1$ row must still decrypt under the v1 derived key (AC1)."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from app.core.config import get_settings

    settings = get_settings()
    secret = settings.auth_secret_key or "job-search-agent-dev-content-key-not-for-prod"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    fernet_v1 = Fernet(key)
    v1_token = ENCRYPTED_PREFIX + fernet_v1.encrypt(_PLAIN_JD.encode("utf-8")).decode("ascii")
    assert decrypt_text(v1_token) == _PLAIN_JD


def test_enc1_and_enc2_coexist() -> None:
    """Mixed enc1$ and enc2$ values must each decrypt correctly (AC2)."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from app.core.config import get_settings

    # Build a v1 token.
    settings = get_settings()
    secret = settings.auth_secret_key or "job-search-agent-dev-content-key-not-for-prod"
    key_v1 = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    fernet_v1 = Fernet(key_v1)
    v1_token = ENCRYPTED_PREFIX + fernet_v1.encrypt(b"v1 content").decode("ascii")

    # Build a v2 token via encrypt_text.
    v2_token = encrypt_text("v2 content")

    assert v1_token.startswith(ENCRYPTED_PREFIX)
    assert v2_token.startswith(ENCRYPTED_PREFIX_V2)
    assert decrypt_text(v1_token) == "v1 content"
    assert decrypt_text(v2_token) == "v2 content"


# ---------------------------------------------------------------------------
# prod fail-fast (AC3)
# ---------------------------------------------------------------------------


def test_prod_missing_content_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """In prod, encrypt_text must raise when CONTENT_ENCRYPTION_KEY is missing."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CONTENT_ENCRYPTION_KEY", "")
    monkeypatch.setenv("AUTH_SECRET_KEY", "some-auth-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    # Also clear the lru_cache on the crypto functions.
    from app.core import content_crypto

    content_crypto._fernet_v1.cache_clear()
    content_crypto._fernet_v2.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="CONTENT_ENCRYPTION_KEY"):
            encrypt_text("sensitive content")
    finally:
        # Restore test env.
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()
        content_crypto._fernet_v1.cache_clear()
        content_crypto._fernet_v2.cache_clear()


def test_prod_missing_content_key_raises_on_decrypt_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In prod, decrypting an enc2$ value must also raise when key is missing."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CONTENT_ENCRYPTION_KEY", "")
    monkeypatch.setenv("AUTH_SECRET_KEY", "some-auth-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.core import content_crypto

    content_crypto._fernet_v1.cache_clear()
    content_crypto._fernet_v2.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="CONTENT_ENCRYPTION_KEY"):
            decrypt_text(ENCRYPTED_PREFIX_V2 + "some-token")
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()
        content_crypto._fernet_v1.cache_clear()
        content_crypto._fernet_v2.cache_clear()


# ---------------------------------------------------------------------------
# Non-prod fallback: v2 falls back to v1 key when key is empty
# ---------------------------------------------------------------------------


def test_nonprod_empty_key_falls_back_to_v1() -> None:
    """In non-prod, an empty CONTENT_ENCRYPTION_KEY falls back to the v1 path.

    The encrypted output still uses the enc2$ prefix (since encrypt_text always
    writes v2), but the underlying Fernet is the v1 derived key. This is the
    expected behavior for local/test environments that haven't configured a
    dedicated content key.
    """
    # In the test env, CONTENT_ENCRYPTION_KEY is unset → fallback to v1 key.
    token = encrypt_text("fallback test")
    assert token.startswith(ENCRYPTED_PREFIX_V2)
    assert decrypt_text(token) == "fallback test"


# ---------------------------------------------------------------------------
# Decryption failure with wrong key (enc2$ with a different key)
# ---------------------------------------------------------------------------


def test_enc2_wrong_key_collapses_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An enc2$ value encrypted under one key must return '' under another."""
    from cryptography.fernet import Fernet

    # Encrypt with a real content key.
    key_a = Fernet.generate_key().decode()
    monkeypatch.setenv("CONTENT_ENCRYPTION_KEY", key_a)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.core import content_crypto

    content_crypto._fernet_v1.cache_clear()
    content_crypto._fernet_v2.cache_clear()
    token = encrypt_text("secret under key A")

    # Now switch to a different key.
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("CONTENT_ENCRYPTION_KEY", key_b)
    get_settings.cache_clear()
    content_crypto._fernet_v1.cache_clear()
    content_crypto._fernet_v2.cache_clear()
    assert decrypt_text(token) == ""  # wrong key → empty, not ciphertext leak

    # Restore.
    monkeypatch.delenv("CONTENT_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    content_crypto._fernet_v1.cache_clear()
    content_crypto._fernet_v2.cache_clear()


# ---------------------------------------------------------------------------
# ORM / API contract
# ---------------------------------------------------------------------------


def test_job_create_persists_encrypted_jd_raw(client: TestClient) -> None:
    """POST /jobs stores ciphertext; responses keep returning plaintext."""
    _ = client  # fixture: truncates tables before the test
    resp = client.post(
        "/api/v1/jobs",
        json={"company": "Acme", "title": "后端工程师", "jd_raw": _PLAIN_JD},
        headers={"X-User-Id": "crypto_user"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["jd_raw"] == _PLAIN_JD  # response stays usable

    with SessionLocal() as db:
        stored = db.execute(
            text(
                "SELECT jd_raw FROM job_postings "
                "WHERE user_id = 'crypto_user' AND company = 'Acme'"
            )
        ).scalar_one()
    assert stored.startswith(ENCRYPTED_PREFIX_V2)
    assert _PLAIN_JD not in stored  # plaintext never reaches disk


def test_job_update_reencrypts_jd_raw(client: TestClient) -> None:
    created = client.post(
        "/api/v1/jobs",
        json={"company": "Beta", "title": "工程师", "jd_raw": "原始 JD 文本内容。"},
        headers={"X-User-Id": "crypto_user2"},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"jd_raw": _PLAIN_JD},
        headers={"X-User-Id": "crypto_user2"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["jd_raw"] == _PLAIN_JD

    with SessionLocal() as db:
        stored = db.execute(
            text("SELECT jd_raw FROM job_postings WHERE id = :id"), {"id": job_id}
        ).scalar_one()
    assert stored.startswith(ENCRYPTED_PREFIX_V2)
    assert _PLAIN_JD not in stored


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
