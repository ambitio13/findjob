"""Redis-backed fixed-window rate limiting middleware.

Three buckets are enforced per client IP, keyed by path class:

- ``auth``      — ``/auth/*`` (brute-force protection on login/register);
- ``model``     — endpoints that trigger LLM calls (request-experience protection);
- ``global``    — every other request.

This is **per-minute request-experience protection** only. The cost defense
line — a daily call budget enforced on every real LLM invocation — lives in
``app/models_gateway/budget.py`` and is enforced by ``BudgetedModelGateway``.

Design choices:

- **Fixed window** (``INCR`` + ``EXPIRE`` on the first hit) — one Redis
  round-trip per request, good enough for this product's concurrency.
- **Fail-open**: any Redis error lets the request through (logged). A Redis
  outage must not take down reads; rate limiting is a cost/abuse guardrail,
  not an authorization boundary.
- Identity is the client IP. Authenticated per-user quotas can be layered on
  later without changing the middleware contract.
"""

from __future__ import annotations

import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("app.api.rate_limit")

#: Path fragments identifying model-call endpoints (LLM cost exposure).
#: Note: this is per-minute request-experience protection only. The daily
#: cost budget is enforced at the gateway layer (app.models_gateway.budget).
_MODEL_PATH_FRAGMENTS: tuple[str, ...] = (
    "/analysis",
    "/match",
    "/communicate",
    "/readiness",
    "/jd-parse",
    "/recommended-jobs/batch",
)

_BUCKET_KEY_PREFIX = "rl"

#: Request paths carry the API prefix (``/api/v1``); buckets are classified on
#: the resource path after the prefix.
_API_PREFIX = "/api/v1"


def classify_bucket(path: str) -> str:
    """Return the rate-limit bucket for a request path."""
    normalized = path[len(_API_PREFIX) :] if path.startswith(_API_PREFIX) else path
    if normalized.startswith("/auth"):
        return "auth"
    if any(fragment in normalized for fragment in _MODEL_PATH_FRAGMENTS):
        return "model"
    return "global"


def _bucket_limit(bucket: str) -> int:
    settings = get_settings()
    return {
        "auth": settings.rate_limit_auth_per_minute,
        "model": settings.rate_limit_model_per_minute,
        "global": settings.rate_limit_global_per_minute,
    }[bucket]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-IP, per-minute fixed-window limits. See module docstring."""

    def __init__(self, app, redis_client=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        # Injected for tests; production resolves the shared client lazily so
        # import never touches the network.
        self._redis_client = redis_client

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Health checks are exempt so orchestrators and dev-health scripts
        # never trip the limiter.
        if request.url.path.endswith("/health"):
            return await call_next(request)

        bucket = classify_bucket(request.url.path)
        limit = _bucket_limit(bucket)
        client_ip = request.client.host if request.client else "unknown"
        window = int(time.time() // 60)
        key = f"{_BUCKET_KEY_PREFIX}:{settings.queue_namespace}:{bucket}:{client_ip}:{window}"

        try:
            redis_client = self._redis_client
            if redis_client is None:
                from app.cache.redis import get_redis_client

                redis_client = get_redis_client().client
            count = redis_client.incr(key)
            if count == 1:
                redis_client.expire(key, 70)  # window + grace so keys always expire
        except Exception:  # noqa: BLE001 — fail open on any Redis fault
            _log.warning("rate_limit.fail_open", bucket=bucket)
            return await call_next(request)

        if count > limit:
            _log.warning(
                "rate_limit.rejected",
                bucket=bucket,
                count=count,
                limit=limit,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": {"reason": "rate_limited", "retry_after_seconds": 60}},
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
