"""arq worker process entrypoint.

Run with the arq CLI::

    arq app.queue.worker.WorkerSettings

The worker is a separate process from the FastAPI app. It connects to the same
Redis (queue transport) and PostgreSQL (durable state) and executes registered
handler functions. Per the design contract:

- handlers open their own DB sessions via :func:`app.db.session.SessionLocal`;
- handlers construct their own model gateway/settings inside the worker;
- no request-scoped session or DI gateway is passed across the queue boundary.

``WorkerSettings`` is a plain class whose attributes are read by arq's
``create_worker`` (only attributes matching :class:`arq.Worker` constructor
parameters are consumed). ``functions`` registers the handlers by name.
"""

from __future__ import annotations

from arq.worker import Function

from app.core.config import get_settings
from app.queue.handlers import (
    jd_paste_parsing,
    resume_aware_jd_analysis,
    resume_fact_extraction,
    smoke,
)
from app.queue.runtime import get_redis_settings

_settings = get_settings()


def _functions() -> list[Function]:
    """Wrap registered handlers into arq :class:`Function` objects.

    Each :class:`Function` binds a handler coroutine to its dispatch name,
    timeout, and retry count. The name MUST equal the handler's
    ``WorkflowPayload.workflow_type`` so :func:`enqueue_workflow` dispatches
    correctly.
    """
    return [
        Function(
            name="smoke",
            coroutine=smoke,
            timeout_s=_settings.queue_job_timeout,
            keep_result_s=3600,
            keep_result_forever=False,
            max_tries=_settings.queue_max_retries + 1,
        ),
        Function(
            name="jd_paste_parsing",
            coroutine=jd_paste_parsing,
            timeout_s=_settings.queue_job_timeout,
            keep_result_s=3600,
            keep_result_forever=False,
            max_tries=_settings.queue_max_retries + 1,
        ),
        Function(
            name="resume_fact_extraction",
            coroutine=resume_fact_extraction,
            timeout_s=_settings.queue_job_timeout,
            keep_result_s=3600,
            keep_result_forever=False,
            max_tries=_settings.queue_max_retries + 1,
        ),
        Function(
            name="resume_aware_jd_analysis",
            coroutine=resume_aware_jd_analysis,
            timeout_s=_settings.queue_job_timeout,
            keep_result_s=3600,
            keep_result_forever=False,
            max_tries=_settings.queue_max_retries + 1,
        ),
    ]


class WorkerSettings:
    """arq worker settings consumed by the ``arq`` CLI.

    Attributes are read by :func:`arq.worker.get_kwargs`; only keys matching
    :class:`arq.Worker` parameters are used, so adding unrelated class
    attributes here is harmless.
    """

    functions = _functions()
    redis_settings = get_redis_settings()
    queue_name = f"{_settings.queue_namespace}:queue"
    max_jobs = 10
    job_timeout = _settings.queue_job_timeout
    max_tries = _settings.queue_max_retries + 1
