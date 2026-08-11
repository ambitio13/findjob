"""Content-at-rest encryption tests (jd_raw privacy remediation).

Covers the envelope helpers and the end-to-end contract: raw JD text sent
through the public API is stored as an ``enc1$`` ciphertext in
``job_postings.jd_raw`` while API responses and pipeline contexts still see
the plaintext (transparent ORM-level encryption).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.content_crypto import ENCRYPTED_PREFIX, decrypt_text, encrypt_text
from app.db.session import SessionLocal

_PLAIN_JD = "负责后端服务设计与开发，要求 3 年以上 Python 经验，薪资 20k-30k/月。"


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip() -> None:
    token = encrypt_text(_PLAIN_JD)
    assert token.startswith(ENCRYPTED_PREFIX)
    assert _PLAIN_JD not in token
    assert decrypt_text(token) == _PLAIN_JD


def test_encrypt_is_not_deterministic() -> None:
    """Fernet carries a timestamp/IV: two encryptions must differ."""
    assert encrypt_text(_PLAIN_JD) != encrypt_text(_PLAIN_JD)


def test_encrypt_never_double_wraps() -> None:
    token = encrypt_text(_PLAIN_JD)
    assert encrypt_text(token) == token


def test_empty_values_pass_through() -> None:
    assert encrypt_text("") == ""
    assert decrypt_text("") == ""


def test_legacy_plaintext_passes_through() -> None:
    """Rows written before the migration still decrypt to themselves."""
    assert decrypt_text(_PLAIN_JD) == _PLAIN_JD


def test_corrupted_envelope_collapses_to_empty() -> None:
    assert decrypt_text(ENCRYPTED_PREFIX + "not-a-valid-token") == ""


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
    assert stored.startswith(ENCRYPTED_PREFIX)
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
    assert stored.startswith(ENCRYPTED_PREFIX)
    assert _PLAIN_JD not in stored


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
