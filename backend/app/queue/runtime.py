"""Queue runtime — Redis pool construction and enqueue helper.

This module is the only place that knows about arq's client API. API routes
call :func:`enqueue_workflow` with a typed payload; they never import arq
directly. The helper:

1. resolves Redis settings from the app ``Settings`` (reuse ``redis_url``);
2. builds/returns an :class:`arq.ArqRedis` pool;
3. enqueues the payload under the worker function named by
   ``payload.workflow_type`` and returns the arq :class:`Job` reference.

Per design.md the durable ``AgentRun`` row is created *before* this helper is
called, so a successful enqueue means both the DB row and the queue entry
exist. If Redis is unavailable the helper raises and the caller leaves the run
in ``queued`` state (or flips it to ``failed`` depending on policy) — Redis is
never the only record that a job was requested.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.config import get_settings
from app.queue.payloads import WorkflowPayload

if TYPE_CHECKING:
    from arq.jobs import Job

_log_name = "app.queue.runtime"


def _redis_settings_from_url(url: str) -> RedisSettings:
    """Translate a ``redis://...`` URL into arq ``RedisSettings``.

    arq's ``RedisSettings`` does not accept a URL string directly, so we parse
    the configured ``redis_url`` (the same one the cache client uses) into the
    host/port/db/password fields arq expects.
    """
    parsed = urlparse(url)
    db = 0
    if parsed.path and parsed.path.lstrip("/"):
        db = int(parsed.path.lstrip("/"))
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=db,
        password=parsed.password,
    )


@lru_cache(maxsize=1)
def get_redis_settings() -> RedisSettings:
    """Return arq ``RedisSettings`` derived from ``Settings.redis_url``.

    Cached because settings are immutable for the process lifetime and
    ``RedisSettings`` is cheap to construct but constructing it repeatedly on
    every enqueue is wasteful.
    """
    return _redis_settings_from_url(get_settings().redis_url)


async def get_queue() -> ArqRedis:
    """Return a connected arq Redis pool.

    The pool is created lazily on first call. ``create_pool`` opens the
    connection; callers should let arq manage pooling rather than closing the
    pool after each enqueue.
    """
    return await create_pool(get_redis_settings())


def _queue_name() -> str:
    """Return the arq queue name, namespaced per ``Settings.queue_namespace``.

    Namespacing lets multiple environments share one Redis instance without
    colliding on the default ``arq:queue`` key.
    """
    return f"{get_settings().queue_namespace}:queue"


async def enqueue_workflow(
    payload: WorkflowPayload,
    *,
    job_id: str | None = None,
) -> Job | None:
    """Enqueue ``payload`` for the worker and return the arq :class:`Job`.

    The worker function name is ``payload.workflow_type``; the worker module
    registers a function under that exact name (see ``app.queue.worker``).

    ``job_id`` may be set to the ``agent_run_id`` (or a derived idempotency
    id) so a duplicate enqueue of the same run is a no-op rather than a second
    job — arq refuses to enqueue a second job with the same ``_job_id``.

    Raises if Redis is unreachable; callers are responsible for surfacing that
    as a failed run rather than leaving the user with a silent spinner.
    """
    pool = await get_queue()
    return await pool.enqueue_job(
        payload.workflow_type,
        payload.model_dump(),
        _job_id=job_id,
        _queue_name=_queue_name(),
    )
