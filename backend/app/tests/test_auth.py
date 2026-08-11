"""Auth API and security-primitive tests (Phase 0 guardrail).

Covers:

- register → login → bearer-authenticated request round-trip;
- the auth user id is reused as the profile id (ownership continuity);
- login failure modes collapse to one ``invalid_credentials`` 401;
- password hash/verify primitives and token sign/verify primitives;
- prod environments refuse the legacy X-User-Id fallback.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import get_settings

_PREFIX = "/api/v1/auth"


def _register(
    client: TestClient, username: str = "alice", password: str = "correct-horse-99"
) -> dict:
    resp = client.post(
        f"{_PREFIX}/register",
        json={"username": username, "password": password, "display_name": "Alice"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(client: TestClient, username: str = "alice", password: str = "correct-horse-99") -> str:
    resp = client.post(f"{_PREFIX}/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def test_password_hash_roundtrip() -> None:
    hashed = security.hash_password("s3cret-passphrase")
    assert security.verify_password("s3cret-passphrase", hashed)
    assert not security.verify_password("wrong", hashed)
    # Malformed hashes never raise, always reject.
    assert not security.verify_password("x", "not-a-valid-hash")
    assert not security.verify_password("x", "md5$1$aa$bb")


def test_password_hash_uses_random_salt() -> None:
    a = security.hash_password("same-password")
    b = security.hash_password("same-password")
    assert a != b  # distinct salts
    assert security.verify_password("same-password", a)
    assert security.verify_password("same-password", b)


def test_token_roundtrip_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    token = security.create_access_token("user123")
    assert security.decode_access_token(token) == "user123"

    # Tampered payload → rejected.
    payload, signature = token.split(".")
    tampered = payload[:-2] + "zz." + signature
    assert security.decode_access_token(tampered) is None

    # Expired token (beyond leeway) → rejected.
    expired = security.create_access_token("user123", ttl_minutes=-1)
    assert security.decode_access_token(expired) is None


def test_token_rejected_with_wrong_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    token = security.create_access_token("user123")
    monkeypatch.setenv("AUTH_SECRET_KEY", "a-different-secret")
    get_settings.cache_clear()
    try:
        assert security.decode_access_token(token) is None
    finally:
        monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-not-for-production")
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# API flow
# ---------------------------------------------------------------------------


def test_register_login_and_authenticated_request(client: TestClient) -> None:
    identity = _register(client)
    token = _login(client)

    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    # The auth user id is the profile id — ownership continuity.
    assert resp.json()["id"] == identity["id"]
    assert resp.json()["display_name"] == "Alice"


def test_register_duplicate_username_conflict(client: TestClient) -> None:
    _register(client)
    resp = client.post(
        f"{_PREFIX}/register",
        json={"username": "alice", "password": "another-pass-99"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "username_taken"


def test_register_validates_username_and_password(client: TestClient) -> None:
    bad_username = client.post(
        f"{_PREFIX}/register", json={"username": "a b!", "password": "correct-horse-99"}
    )
    assert bad_username.status_code == 422

    short_password = client.post(
        f"{_PREFIX}/register", json={"username": "bob", "password": "short"}
    )
    assert short_password.status_code == 422


def test_login_wrong_password_is_401(client: TestClient) -> None:
    _register(client)
    resp = client.post(f"{_PREFIX}/login", json={"username": "alice", "password": "wrong-wrong-99"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "invalid_credentials"


def test_login_unknown_user_is_same_401(client: TestClient) -> None:
    resp = client.post(f"{_PREFIX}/login", json={"username": "ghost", "password": "whatever-99"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "invalid_credentials"


def test_invalid_bearer_token_falls_back_to_demo_in_test_env(client: TestClient) -> None:
    # Test env keeps the legacy fallback: an invalid token degrades to the
    # demo user instead of 401 (prod behaviour is covered separately).
    resp = client.get("/api/v1/users/me", headers={"Authorization": "Bearer garbage.token"})
    assert resp.status_code == 200
    assert resp.json()["id"] == get_settings().demo_user_id


def test_prod_refuses_demo_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()
    try:
        resp = client.get("/api/v1/users/me")
        assert resp.status_code == 401
        assert resp.json()["detail"]["reason"] == "authentication_required"

        # But a real token still works in prod.
        _register(client)
        token = _login(client)
        ok = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()


def test_token_for_deleted_user_stops_working(client: TestClient) -> None:
    identity = _register(client)
    token = _login(client)

    from sqlalchemy import text

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.execute(text("DELETE FROM user_profiles WHERE id = :id"), {"id": identity["id"]})
        db.execute(text("DELETE FROM auth_users WHERE id = :id"), {"id": identity["id"]})
        db.commit()

    # Token signature is still valid, but the subject no longer exists. In the
    # test env this degrades to the demo fallback rather than 401.
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] != identity["id"]


def test_expired_token_rejected(client: TestClient) -> None:
    _register(client)
    # Issue a token with a negative TTL directly (login always uses the TTL
    # setting, so we bypass it to simulate expiry).
    expired = security.create_access_token("some-user", ttl_minutes=-1)
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {expired}"})
    # Test env fallback → demo user, not the claimed subject.
    assert resp.status_code == 200
    assert resp.json()["id"] == get_settings().demo_user_id
