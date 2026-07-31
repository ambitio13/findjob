"""Redis client module.

Redis is used for cache, locks, queues, rate limits, and hot session context.
It is never the source of truth; durable state lives in PostgreSQL.
"""

from __future__ import annotations

import redis

from app.core.config import get_settings

_settings = get_settings()


def get_redis() -> redis.Redis:
    """Return a shared Redis client. The connection is lazy."""
    return redis.Redis.from_url(
        _settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
    )


class RedisClient:
    """Thin wrapper exposing the redis client as a dependency-injectable object.

    This keeps route handlers and services from importing ``redis`` directly and
    makes it trivial to swap for a fake in tests.
    """

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._client = client or get_redis()

    @property
    def client(self) -> redis.Redis:
        return self._client

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            return False


_default_client: RedisClient | None = None


def get_redis_client() -> RedisClient:
    global _default_client
    if _default_client is None:
        _default_client = RedisClient()
    return _default_client
