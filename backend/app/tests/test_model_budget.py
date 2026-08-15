"""Tests for the model gateway daily call-budget circuit breaker.

These tests use an injectable fake Redis (mirroring ``test_rate_limit.py``'s
``FakeRedis`` pattern) so no network or real Redis is required. They cover:

- under-limit calls pass through to the inner gateway;
- over-limit calls raise ``ModelBudgetExceeded`` and set the tripped marker;
- Redis errors fail-open by default (call proceeds);
- ``is_tripped_today`` reflects the marker;
- ``BudgetedModelGateway`` wraps both ``chat`` and ``structured``;
- the HTTP exception handler returns 429 with ``Retry-After``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.models_gateway.budget import (
    BUDGET_EXCEEDED_ERROR,
    BudgetedModelGateway,
    ModelBudgetExceeded,
    ensure_budget,
    is_tripped_today,
)

# ---------------------------------------------------------------------------
# Fake Redis (mirrors test_rate_limit.py)
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory stand-in for the redis client surface we use."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.keys: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.keys[key] = value
        if ex is not None:
            self.ttl[key] = ex

    def exists(self, key: str) -> int:
        return 1 if key in self.keys else 0


class BrokenRedis:
    def incr(self, key: str) -> int:
        raise ConnectionError("redis down")

    def expire(self, key: str, seconds: int) -> None:
        raise ConnectionError("redis down")

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise ConnectionError("redis down")

    def exists(self, key: str) -> int:
        raise ConnectionError("redis down")


# ---------------------------------------------------------------------------
# Test gateway stub
# ---------------------------------------------------------------------------


class _StubGateway(ModelGateway):
    """Returns a canned ChatResponse so we can verify pass-through."""

    provider_name = "stub"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="stub-response",
            model="stub-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=1,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    async def structured(self, request):  # type: ignore[no-untyped-def]
        return {"structured": "response"}


# ---------------------------------------------------------------------------
# Settings fixture: small limit for deterministic testing
# ---------------------------------------------------------------------------


@pytest.fixture()
def small_budget(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRedis]:
    """Configure a small daily call limit and yield a fresh FakeRedis."""
    monkeypatch.setenv("MODEL_DAILY_CALL_LIMIT", "3")
    monkeypatch.setenv("MODEL_DAILY_BUDGET_ENABLED", "true")
    monkeypatch.setenv("MODEL_BUDGET_FAIL_OPEN", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    fake = FakeRedis()
    yield fake
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# ensure_budget unit tests
# ---------------------------------------------------------------------------


def test_ensure_budget_under_limit_passes(small_budget: FakeRedis) -> None:
    """Calls under the limit should return incrementing counts."""
    assert ensure_budget(redis_client=small_budget) == 1
    assert ensure_budget(redis_client=small_budget) == 2
    assert ensure_budget(redis_client=small_budget) == 3


def test_ensure_budget_over_limit_raises(small_budget: FakeRedis) -> None:
    """The call that exceeds the limit should raise ModelBudgetExceeded."""
    for _ in range(3):
        ensure_budget(redis_client=small_budget)
    with pytest.raises(ModelBudgetExceeded) as exc_info:
        ensure_budget(redis_client=small_budget)
    assert exc_info.value.count == 4
    assert exc_info.value.limit == 3


def test_ensure_budget_sets_tripped_marker(small_budget: FakeRedis) -> None:
    """Exceeding the limit should set the tripped marker key."""
    for _ in range(3):
        ensure_budget(redis_client=small_budget)
    with pytest.raises(ModelBudgetExceeded):
        ensure_budget(redis_client=small_budget)
    assert is_tripped_today(redis_client=small_budget) is True


def test_ensure_budget_tripped_logs_error(small_budget: FakeRedis) -> None:
    """Exceeding the limit should log at ERROR level (D4 observability).

    structlog is configured with ``PrintLoggerFactory`` which writes directly
    to stdout (bypassing the stdlib logging tree), so we capture stdout to
    verify the event and its level.
    """
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        for _ in range(3):
            ensure_budget(redis_client=small_budget)
        with pytest.raises(ModelBudgetExceeded):
            ensure_budget(redis_client=small_budget)

    output = buf.getvalue()
    # The tripped event must appear and be logged at error level.
    assert "model_budget.tripped" in output
    assert "error" in output.lower()


def test_ensure_budget_not_tripped_under_limit(small_budget: FakeRedis) -> None:
    """Under the limit, is_tripped_today should be False."""
    ensure_budget(redis_client=small_budget)
    assert is_tripped_today(redis_client=small_budget) is False


def test_ensure_budget_ttl_set_on_first_incr(small_budget: FakeRedis) -> None:
    """The first INCR should set a TTL so the key expires."""
    ensure_budget(redis_client=small_budget)
    # At least one key should have a TTL set.
    assert len(small_budget.ttl) > 0
    for ttl in small_budget.ttl.values():
        assert ttl > 0


def test_ensure_budget_disabled_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the budget is disabled, ensure_budget should be a no-op."""
    monkeypatch.setenv("MODEL_DAILY_BUDGET_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        fake = FakeRedis()
        assert ensure_budget(redis_client=fake) == 0
        assert fake.counters == {}  # never touched Redis
    finally:
        get_settings.cache_clear()


def test_ensure_budget_fail_open_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Redis error with fail-open, ensure_budget should return 0."""
    monkeypatch.setenv("MODEL_DAILY_BUDGET_ENABLED", "true")
    monkeypatch.setenv("MODEL_BUDGET_FAIL_OPEN", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert ensure_budget(redis_client=BrokenRedis()) == 0
    finally:
        get_settings.cache_clear()


def test_ensure_budget_fail_closed_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Redis error with fail-closed, ensure_budget should raise."""
    monkeypatch.setenv("MODEL_DAILY_BUDGET_ENABLED", "true")
    monkeypatch.setenv("MODEL_BUDGET_FAIL_OPEN", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(ModelBudgetExceeded):
            ensure_budget(redis_client=BrokenRedis())
    finally:
        get_settings.cache_clear()


def test_is_tripped_today_fail_open_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Redis error, is_tripped_today should return False (no false alarm)."""
    monkeypatch.setenv("MODEL_DAILY_BUDGET_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert is_tripped_today(redis_client=BrokenRedis()) is False
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# BudgetedModelGateway decorator tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budgeted_gateway_chat_passes_under_limit(
    small_budget: FakeRedis,
) -> None:
    """Under the limit, chat should delegate to the inner gateway."""
    gateway = BudgetedModelGateway(_StubGateway(), redis_client=small_budget)
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    resp = await gateway.chat(request)
    assert resp.content == "stub-response"
    assert resp.provider == "stub"  # provider_name preserved


@pytest.mark.asyncio
async def test_budgeted_gateway_chat_raises_over_limit(
    small_budget: FakeRedis,
) -> None:
    """Over the limit, chat should raise before calling the inner gateway."""
    gateway = BudgetedModelGateway(_StubGateway(), redis_client=small_budget)
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    # Exhaust the budget.
    for _ in range(3):
        await gateway.chat(request)
    with pytest.raises(ModelBudgetExceeded):
        await gateway.chat(request)


@pytest.mark.asyncio
async def test_budgeted_gateway_structured_raises_over_limit(
    small_budget: FakeRedis,
) -> None:
    """Over the limit, structured should also raise."""
    from pydantic import BaseModel

    from app.models_gateway.base import StructuredRequest

    class _Out(BaseModel):
        x: int = 0

    gateway = BudgetedModelGateway(_StubGateway(), redis_client=small_budget)
    request = StructuredRequest(messages=[{"role": "user", "content": "hi"}], response_model=_Out)

    # Exhaust the budget via chat, then try structured.
    chat_req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    for _ in range(3):
        await gateway.chat(chat_req)
    with pytest.raises(ModelBudgetExceeded):
        await gateway.structured(request)


# ---------------------------------------------------------------------------
# HTTP exception handler test
# ---------------------------------------------------------------------------


def test_budget_exceeded_http_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global exception handler should return 429 with Retry-After."""
    monkeypatch.setenv("MODEL_DAILY_BUDGET_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import create_app

        app = create_app()

        @app.get("/_test_budget")
        async def _trigger() -> None:
            raise ModelBudgetExceeded(count=501, limit=500, date="20260815")

        client = TestClient(app)
        resp = client.get("/_test_budget")
        assert resp.status_code == 429
        body = resp.json()
        assert body["detail"]["reason"] == "model_budget_exceeded"
        assert "retry_after_seconds" in body["detail"]
        assert "Retry-After" in resp.headers
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Factory wrapping test
# ---------------------------------------------------------------------------


def test_factory_returns_budgeted_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_model_gateway() should always return a BudgetedModelGateway."""
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        from app.models_gateway.factory import get_model_gateway

        gw = get_model_gateway()
        assert isinstance(gw, BudgetedModelGateway)
        assert gw.provider_name == "fake"  # inner provider preserved
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Worker handler error constant test
# ---------------------------------------------------------------------------


def test_budget_exceeded_error_constant() -> None:
    """The sanitized error constant must be the expected string."""
    assert BUDGET_EXCEEDED_ERROR == "model budget exceeded"
