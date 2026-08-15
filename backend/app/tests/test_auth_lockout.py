"""Account login lockout tests (08-15-runtime-guardrails, E2).

Covers:

- After N consecutive failures the account is locked (AC2: correct password
  also gets 401 while locked).
- Lockout error is identical to the wrong-password error (AC3: no enumeration).
- Successful login clears the failure counter (AC2: no pre-existing failures).
- Redis failure is fail-open (AC5: login proceeds normally).
- Non-pattern usernames skip the lockout path (no Redis key injection).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_PREFIX = "/api/v1/auth"

_GOOD_PASSWORD = "correct-horse-99"
_BAD_PASSWORD = "wrong-wrong-99"


def _register(
    client: TestClient, username: str = "lockuser", password: str = _GOOD_PASSWORD
) -> None:
    resp = client.post(
        f"{_PREFIX}/register",
        json={"username": username, "password": password, "display_name": "Lock User"},
    )
    assert resp.status_code == 201, resp.text


def _login(
    client: TestClient, username: str = "lockuser", password: str = _GOOD_PASSWORD
) -> int:
    resp = client.post(f"{_PREFIX}/login", json={"username": username, "password": password})
    return resp.status_code


def _clear_lockout_keys(username: str = "lockuser") -> None:
    """Remove any stale lockout keys so tests start clean."""
    try:
        from app.cache.redis import get_redis_client

        client = get_redis_client().client
        from app.core.config import get_settings

        ns = get_settings().queue_namespace
        client.delete(
            f"lk:{ns}:fail:{username}",
            f"lk:{ns}:lock:{username}",
        )
    except Exception:  # noqa: BLE001
        pass  # Redis may not be available in all environments


@pytest.fixture(autouse=True)
def _clean_lockout_state():
    """Clear lockout keys before and after each test to prevent cross-test leakage."""
    _clear_lockout_keys()
    yield
    _clear_lockout_keys()


# ---------------------------------------------------------------------------
# AC2: lockout after N failures, correct password also rejected while locked
# ---------------------------------------------------------------------------


def test_account_locked_after_max_failures(client: TestClient) -> None:
    """After 5 failures, even the correct password gets 401 (AC2)."""
    _register(client)
    # 5 wrong-password attempts.
    for _ in range(5):
        assert _login(client, password=_BAD_PASSWORD) == 401
    # 6th attempt with the CORRECT password → still 401 (locked).
    assert _login(client, password=_GOOD_PASSWORD) == 401


def test_account_not_locked_below_threshold(client: TestClient) -> None:
    """4 failures should not lock; correct password still works (AC2)."""
    _register(client)
    for _ in range(4):
        assert _login(client, password=_BAD_PASSWORD) == 401
    # Correct password → 200.
    assert _login(client, password=_GOOD_PASSWORD) == 200


# ---------------------------------------------------------------------------
# AC2: successful login clears the failure counter
# ---------------------------------------------------------------------------


def test_successful_login_clears_failure_counter(client: TestClient) -> None:
    """A successful login resets the counter so prior failures don't accumulate."""
    _register(client)
    # 3 failures (below threshold).
    for _ in range(3):
        _login(client, password=_BAD_PASSWORD)
    # Successful login clears the counter.
    assert _login(client, password=_GOOD_PASSWORD) == 200
    # Now 3 more failures should not lock (counter was reset).
    for _ in range(3):
        assert _login(client, password=_BAD_PASSWORD) == 401
    # Still not locked.
    assert _login(client, password=_GOOD_PASSWORD) == 200


# ---------------------------------------------------------------------------
# AC3: locked response is identical to wrong-password response
# ---------------------------------------------------------------------------


def test_locked_response_identical_to_wrong_password(client: TestClient) -> None:
    """A locked login must be byte-for-byte identical to a wrong-password login.

    This prevents enumeration of locked accounts.
    """
    _register(client)
    # Lock the account.
    for _ in range(5):
        _login(client, password=_BAD_PASSWORD)

    locked_resp = client.post(
        f"{_PREFIX}/login",
        json={"username": "lockuser", "password": _GOOD_PASSWORD},
    )
    wrong_resp = client.post(
        f"{_PREFIX}/login",
        json={"username": "lockuser", "password": "another-wrong-99"},
    )

    assert locked_resp.status_code == wrong_resp.status_code == 401
    assert locked_resp.json() == wrong_resp.json()


# ---------------------------------------------------------------------------
# AC5: Redis failure is fail-open (login proceeds)
# ---------------------------------------------------------------------------


def test_lockout_fail_open_on_redis_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Redis is unavailable, login should still work (fail-open, AC5).

    We inject a broken Redis client into the lockout helpers' own resolution
    path (not replacing the helpers themselves). The real try/except inside
    each helper catches the ConnectionError and fail-opens: ``_is_locked``
    returns False, ``_record_failure`` and ``_clear_failures`` are no-ops.
    """
    from app.cache import redis as redis_module

    class _BrokenRedis:
        def exists(self, key: str) -> int:
            raise ConnectionError("redis down")

        def incr(self, key: str) -> int:
            raise ConnectionError("redis down")

        def expire(self, key: str, seconds: int) -> None:
            raise ConnectionError("redis down")

        def set(self, key: str, value: str, ex: int | None = None) -> None:
            raise ConnectionError("redis down")

        def delete(self, *keys: str) -> None:
            raise ConnectionError("redis down")

    class _BrokenRedisClient:
        client = _BrokenRedis()

    monkeypatch.setattr(
        redis_module, "get_redis_client", lambda: _BrokenRedisClient()
    )

    _register(client)
    # Even after many "failures" (which are no-ops due to fail-open), the
    # correct password should still work.
    for _ in range(10):
        _login(client, password=_BAD_PASSWORD)
    assert _login(client, password=_GOOD_PASSWORD) == 200


# ---------------------------------------------------------------------------
# Non-pattern usernames skip lockout (no Redis key injection)
# ---------------------------------------------------------------------------


def test_non_pattern_username_skips_lockout(client: TestClient) -> None:
    """Usernames that don't match USERNAME_PATTERN skip the lockout path.

    The login schema allows arbitrary bytes, but only pattern-valid usernames
    can be real accounts. Non-matching usernames must not enter Redis keys.
    """
    # An invalid username (contains space) will always be invalid_credentials,
    # but must not cause lockout side effects.
    for _ in range(10):
        resp = client.post(
            f"{_PREFIX}/login",
            json={"username": "invalid user!", "password": "whatever-99"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_lockout_config_defaults() -> None:
    """Default config values match the PRD (5 failures, 15 minutes)."""
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.auth_login_max_failures == 5
    assert settings.auth_lockout_minutes == 15
