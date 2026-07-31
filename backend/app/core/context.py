"""Request-scoped current-user context.

This module provides a ``ContextVar`` mirror of the current user id. It exists
as a convenience for non-route code (repositories, agent workers) that needs
the user id without threading it through every signature.

The PRIMARY injection path is still the ``get_current_user`` FastAPI dependency
in ``app.api.deps``: route handlers MUST take the ``UserProfile`` via Depends
for explicit ownership. This ContextVar is read-only convenience and must be
cleared at the end of each request (the dependency does so in its ``finally``).

NOTE: We intentionally avoid ``ContextVar.reset(token)`` here. FastAPI's
``TestClient`` (and Starlette's sync-to-async bridge) can run the dependency
and its ``finally`` cleanup in different ``contextvars.Context`` instances,
which makes ``reset`` raise ``ValueError: was created in a different Context``.
Setting a fresh value and clearing with ``set(None)`` is context-agnostic.
"""

from __future__ import annotations

from contextvars import ContextVar

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: str) -> None:
    """Set the current user id for this context."""
    _current_user_id.set(user_id)


def clear_current_user_id() -> None:
    """Clear the current user id (call in the dependency's ``finally``)."""
    _current_user_id.set(None)


def current_user_id() -> str | None:
    """Read the current user id, if set. Returns ``None`` outside a request."""
    return _current_user_id.get()
