"""BOSS trace-field helpers for structured logging.

This module provides request/worker-scoped ContextVars for the BOSS
semi-automated communicate trace. The trace spans:

    inspect → match → prepare → approval → bridge instruction →
    userscript result → execute outcome

Each segment should carry the stable IDs defined here so a failed run can be
reconstructed end-to-end from logs alone.

Why ContextVars: ``structlog.contextvars.merge_contextvars`` is already wired
into the processor chain (see :func:`app.core.logging.configure_logging`).
Any value bound via :func:`bind_boss_trace` (or the individual setters) flows
into *every* log line emitted in that context — no per-call-site plumbing
needed. The bind/clear helpers keep the discipline at request/worker
boundaries.

Field contract (design.md + prd.md R1):

- ``agent_run_id`` — the durable ``AgentRun`` row id. Cross-segment primary key.
- ``workflow_type`` — the workflow type literal (e.g. ``boss_match``,
  ``platform_guided_submit_prepare``).
- ``user_id`` — the owning user.
- ``application_id`` — the ``ApplicationRecord`` id (inspect onward).
- ``job_id`` — the ``JobPosting`` id.
- ``page_id`` — the connected BOSS tab id (bridge side).
- ``page_url_hash`` — hash of the target page URL (never the raw URL).
- ``instruction_id`` — the bridge instruction id (one per channel round-trip).
- ``failure_code`` — stable machine code on failure paths.

Fields are allowed to be missing (early inspect has no ``application_id``),
but their meaning must never drift. Sensitive values (raw URL, cookie, token,
resume text, HR message body) must never be bound — use hashes, lengths, or
enum outcomes instead.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

# Each trace field gets its own ContextVar so callers can bind a subset
# without clearing the rest. ``default=None`` means "not set in this context".
# We ALSO mirror each value into structlog's own contextvars layer so that
# ``merge_contextvars`` (already wired in the processor chain) flows the trace
# fields into every log line without per-call-site plumbing.
_agent_run_id: ContextVar[str | None] = ContextVar("boss_agent_run_id", default=None)
_workflow_type: ContextVar[str | None] = ContextVar("boss_workflow_type", default=None)
_boss_user_id: ContextVar[str | None] = ContextVar("boss_user_id", default=None)
_application_id: ContextVar[str | None] = ContextVar("boss_application_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("boss_job_id", default=None)
_page_id: ContextVar[str | None] = ContextVar("boss_page_id", default=None)
_page_url_hash: ContextVar[str | None] = ContextVar("boss_page_url_hash", default=None)
_instruction_id: ContextVar[str | None] = ContextVar("boss_instruction_id", default=None)
_failure_code: ContextVar[str | None] = ContextVar("boss_failure_code", default=None)

#: The complete set of trace field names, for assertions and contract tests.
BOSS_TRACE_FIELDS: frozenset[str] = frozenset(
    {
        "agent_run_id",
        "workflow_type",
        "user_id",
        "application_id",
        "job_id",
        "page_id",
        "page_url_hash",
        "instruction_id",
        "failure_code",
    }
)


def bind_boss_trace(
    *,
    agent_run_id: str | None = None,
    workflow_type: str | None = None,
    user_id: str | None = None,
    application_id: str | None = None,
    job_id: str | None = None,
    page_id: str | None = None,
    page_url_hash: str | None = None,
    instruction_id: str | None = None,
    failure_code: str | None = None,
) -> None:
    """Bind BOSS trace fields into the current context.

    Only non-``None`` arguments are set; existing values for omitted arguments
    are preserved. Call :func:`clear_boss_trace` at the boundary (request
    ``finally`` / worker exit) so a stale trace does not leak into the next
    unit of work.

    The fields are bound both into module-local ContextVars (for snapshot
    access via :func:`boss_trace_context`) and into structlog's own context
    vars layer so that ``merge_contextvars`` flows them into every log line.

    Never pass raw URLs, cookies, tokens, resume text, or HR message bodies —
    use ``sanitize_url`` for URL-derived fields and omit sensitive content
    entirely.
    """
    bindings: dict[str, Any] = {}
    if agent_run_id is not None:
        _agent_run_id.set(agent_run_id)
        bindings["agent_run_id"] = agent_run_id
    if workflow_type is not None:
        _workflow_type.set(workflow_type)
        bindings["workflow_type"] = workflow_type
    if user_id is not None:
        _boss_user_id.set(user_id)
        bindings["user_id"] = user_id
    if application_id is not None:
        _application_id.set(application_id)
        bindings["application_id"] = application_id
    if job_id is not None:
        _job_id.set(job_id)
        bindings["job_id"] = job_id
    if page_id is not None:
        _page_id.set(page_id)
        bindings["page_id"] = page_id
    if page_url_hash is not None:
        _page_url_hash.set(page_url_hash)
        bindings["page_url_hash"] = page_url_hash
    if instruction_id is not None:
        _instruction_id.set(instruction_id)
        bindings["instruction_id"] = instruction_id
    if failure_code is not None:
        _failure_code.set(failure_code)
        bindings["failure_code"] = failure_code
    if bindings:
        bind_contextvars(**bindings)


def clear_boss_trace() -> None:
    """Clear all BOSS trace fields from the current context.

    Uses ``set(None)`` rather than ``ContextVar.reset(token)`` to stay
    context-agnostic (mirrors :mod:`app.core.context` — FastAPI's sync-to-async
    bridge can run setup and cleanup in different ``contextvars.Context``
    instances, which makes ``reset`` raise ``ValueError``).
    """
    _agent_run_id.set(None)
    _workflow_type.set(None)
    _boss_user_id.set(None)
    _application_id.set(None)
    _job_id.set(None)
    _page_id.set(None)
    _page_url_hash.set(None)
    _instruction_id.set(None)
    _failure_code.set(None)
    # Clear the structlog contextvars layer too so trace fields don't leak
    # past the boundary. clear_contextvars wipes ALL structlog-bound vars,
    # which is safe at request/worker boundaries where BOSS is the only
    # consumer of structlog contextvars.
    clear_contextvars()


def set_failure_code(code: str | None) -> None:
    """Bind (or clear with ``None``) the failure code for the current context."""
    _failure_code.set(code)
    if code is not None:
        bind_contextvars(failure_code=code)
    else:
        from structlog.contextvars import unbind_contextvars

        unbind_contextvars("failure_code")


def boss_trace_context() -> dict[str, Any]:
    """Return a snapshot of the currently-bound BOSS trace fields.

    Omits ``None`` values so logs stay compact. Useful for explicit ``_log``
    calls that need the trace dict as kwargs, or for assertions in tests.
    """
    snapshot: dict[str, Any] = {}
    for name, var in (
        ("agent_run_id", _agent_run_id),
        ("workflow_type", _workflow_type),
        ("user_id", _boss_user_id),
        ("application_id", _application_id),
        ("job_id", _job_id),
        ("page_id", _page_id),
        ("page_url_hash", _page_url_hash),
        ("instruction_id", _instruction_id),
        ("failure_code", _failure_code),
    ):
        value = var.get()
        if value is not None:
            snapshot[name] = value
    return snapshot


def bound_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger pre-bound with the current BOSS trace fields.

    Prefer :func:`bind_boss_trace` for the common case — it flows into every
    logger via ``merge_contextvars`` without per-logger binding. Use this
    helper only when a one-off logger needs the trace dict and the global
    context is not yet bound (e.g. a library entry point).
    """
    from app.core.logging import get_logger

    return get_logger(name).bind(**boss_trace_context())
