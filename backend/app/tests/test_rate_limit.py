"""Rate-limit middleware and bucket-classification tests (Phase 0 guardrail).

The middleware fails open when Redis is unavailable; these tests use injected
fake clients to exercise both the enforcing and fail-open paths without a
running Redis.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit import RateLimitMiddleware, classify_bucket

# ---------------------------------------------------------------------------
# Bucket classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "bucket"),
    [
        ("/api/v1/auth/login", "auth"),
        ("/api/v1/auth/register", "auth"),
        ("/api/v1/jobs/job123/analysis", "model"),
        ("/api/v1/boss/match", "model"),
        ("/api/v1/applications/a1/readiness/run", "model"),
        ("/api/v1/applications", "global"),
        ("/api/v1/users/me", "global"),
        ("/health", "global"),
    ],
)
def test_classify_bucket(path: str, bucket: str) -> None:
    assert classify_bucket(path) == bucket


# ---------------------------------------------------------------------------
# Middleware behaviour
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory stand-in for the redis client surface we use."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key: str, seconds: int) -> None:  # noqa: ARG002
        pass


class BrokenRedis:
    def incr(self, key: str) -> int:
        raise ConnectionError("redis down")

    def expire(self, key: str, seconds: int) -> None:
        raise ConnectionError("redis down")


def _make_app(fake_redis: object) -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(RateLimitMiddleware, redis_client=fake_redis)
    return TestClient(app)


def test_limiter_enforces_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_PER_MINUTE", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        client = _make_app(FakeRedis())
        for _ in range(3):
            assert client.get("/api/v1/ping").status_code == 200
        blocked = client.get("/api/v1/ping")
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["reason"] == "rate_limited"
        assert blocked.headers["Retry-After"] == "60"
    finally:
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # keep test default
        monkeypatch.delenv("RATE_LIMIT_GLOBAL_PER_MINUTE", raising=False)
        get_settings.cache_clear()


def test_limiter_fails_open_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_app(BrokenRedis())
    # Every request passes through despite Redis errors.
    for _ in range(5):
        assert client.get("/api/v1/ping").status_code == 200


def test_limiter_disabled_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        fake = FakeRedis()
        client = _make_app(fake)
        assert client.get("/api/v1/ping").status_code == 200
        assert fake.counters == {}  # never touched
    finally:
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # keep test default
        get_settings.cache_clear()


def test_health_exempt_from_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_PER_MINUTE", "1")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        fake = FakeRedis()
        app = FastAPI()

        @app.get("/api/v1/health")
        def health() -> dict[str, str]:
            return {"status": "ok"}

        app.add_middleware(RateLimitMiddleware, redis_client=fake)
        client = TestClient(app)
        for _ in range(3):
            assert client.get("/api/v1/health").status_code == 200
        assert fake.counters == {}
    finally:
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # keep test default
        monkeypatch.delenv("RATE_LIMIT_GLOBAL_PER_MINUTE", raising=False)
        get_settings.cache_clear()
