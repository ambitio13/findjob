"""Daily call-budget circuit breaker for the model gateway.

This module implements the cost defense line: every real LLM call (``chat``
and ``structured``) increments a Redis daily counter; when the configured
``model_daily_call_limit`` is exceeded, a :class:`ModelBudgetExceeded` is
raised before the provider is ever contacted.

Design (see .trellis/tasks/08-15-model-gateway-budget-circuit/design.md):

- **INCR-then-check**: rejected attempts also consume the counter. This
  prevents post-limit retry storms from hammering the provider and keeps the
  semantics simple ("budget counts call attempts").
- **Daily UTC key**: ``mb:{queue_namespace}:daily:{YYYYmmdd}`` with a 48h TTL
  so cross-time-zone windows are covered.
- **Tripped marker**: ``mb:{queue_namespace}:tripped:{YYYYmmdd}`` is set when
  the limit is first hit, exposing ``is_tripped_today()`` for the health
  endpoint (task #5).
- **Fail-open by default**: a Redis outage lets calls through (mirrors
  :class:`~app.api.rate_limit.RateLimitMiddleware`). The provider-side API key
  quota is the mandatory second line of defense.

The :class:`BudgetedModelGateway` decorator wraps any concrete
:class:`~app.models_gateway.base.ModelGateway` so that both the HTTP path and
the worker path are covered from a single factory point.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models_gateway.base import (
    ChatRequest,
    ChatResponse,
    ModelGateway,
    StructuredRequest,
)

T = TypeVar("T")

_log = get_logger("app.models_gateway.budget")

#: TTL for daily and tripped keys — 48h covers any timezone window.
_KEY_TTL_SECONDS = 172800

#: Sentinel for the sanitized error message persisted on failed runs.
BUDGET_EXCEEDED_ERROR = "model budget exceeded"


class ModelBudgetExceeded(Exception):
    """Raised when the daily model call budget has been exhausted.

    Carries the count, limit, and UTC date string so callers (HTTP handler,
    worker) can produce a sanitized error without leaking internal details.
    """

    def __init__(self, *, count: int, limit: int, date: str) -> None:
        self.count = count
        self.limit = limit
        self.date = date
        super().__init__(f"model budget exceeded: {count}/{limit} on {date}")


def _daily_key(date: str | None = None) -> str:
    """Return the Redis daily counter key for the given (or today's) UTC date."""
    settings = get_settings()
    if date is None:
        date = datetime.now(UTC).strftime("%Y%m%d")
    return f"mb:{settings.queue_namespace}:daily:{date}"


def _tripped_key(date: str | None = None) -> str:
    """Return the Redis tripped-marker key for the given (or today's) UTC date."""
    settings = get_settings()
    if date is None:
        date = datetime.now(UTC).strftime("%Y%m%d")
    return f"mb:{settings.queue_namespace}:tripped:{date}"


def _seconds_until_utc_midnight() -> int:
    """Seconds remaining until the next UTC midnight (for Retry-After)."""
    now = datetime.now(UTC)
    tomorrow = (now.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp() + 86400
    return max(1, int(tomorrow - now.timestamp()))


def ensure_budget(
    *,
    redis_client: Any = None,
) -> int:
    """Increment the daily counter and return the new count.

    If the count exceeds the limit, a :class:`ModelBudgetExceeded` is raised
    and the tripped marker is set. When ``model_daily_budget_enabled`` is
    ``False`` this is a no-op (returns 0).

    On Redis errors the behaviour depends on ``model_budget_fail_open``:
    ``True`` (default) logs and returns 0 (call proceeds); ``False`` re-raises
    a :class:`ModelBudgetExceeded` to reject the call.
    """
    settings = get_settings()
    if not settings.model_daily_budget_enabled:
        return 0

    date = datetime.now(UTC).strftime("%Y%m%d")
    limit = settings.model_daily_call_limit

    try:
        client = redis_client
        if client is None:
            from app.cache.redis import get_redis_client

            client = get_redis_client().client

        key = _daily_key(date)
        count = client.incr(key)
        if count == 1:
            client.expire(key, _KEY_TTL_SECONDS)

        if count > limit:
            # Set the tripped marker so is_tripped_today() reports True even
            # before the next call attempt.
            tripped = _tripped_key(date)
            client.set(tripped, "1", ex=_KEY_TTL_SECONDS)
            _log.error(
                "model_budget.tripped",
                count=count,
                limit=limit,
                date=date,
            )
            raise ModelBudgetExceeded(count=count, limit=limit, date=date)

        return count
    except ModelBudgetExceeded:
        raise
    except Exception:
        if settings.model_budget_fail_open:
            _log.warning("model_budget.redis_error", action="fail_open")
            return 0
        # Fail-closed: reject rather than risk an uncounted LLM call.
        _log.warning("model_budget.redis_error", action="fail_closed")
        raise ModelBudgetExceeded(count=0, limit=limit, date=date) from None


def is_tripped_today(*, redis_client: Any = None) -> bool:
    """Return ``True`` if the daily budget has been tripped today (UTC).

    This is a read-only probe for the health endpoint (task #5). On Redis
    errors it returns ``False`` (fail-open: do not report a false alarm).
    """
    settings = get_settings()
    if not settings.model_daily_budget_enabled:
        return False

    date = datetime.now(UTC).strftime("%Y%m%d")
    try:
        client = redis_client
        if client is None:
            from app.cache.redis import get_redis_client

            client = get_redis_client().client
        return bool(client.exists(_tripped_key(date)))
    except Exception:
        _log.warning("model_budget.redis_error", action="is_tripped_fail_open")
        return False


class BudgetedModelGateway(ModelGateway):
    """Decorator that wraps a concrete gateway with daily budget enforcement.

    The wrapped gateway's ``provider_name`` is preserved so downstream
    observability (logs, response.raw) is unaffected.
    """

    def __init__(self, inner: ModelGateway, *, redis_client: Any = None) -> None:
        self._inner = inner
        self._redis_client = redis_client
        self.provider_name = inner.provider_name

    async def chat(self, request: ChatRequest) -> ChatResponse:
        ensure_budget(redis_client=self._redis_client)
        return await self._inner.chat(request)

    async def structured(self, request: StructuredRequest[T]) -> T:
        ensure_budget(redis_client=self._redis_client)
        return await self._inner.structured(request)
