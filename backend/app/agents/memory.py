"""Memory abstraction for the agent runtime.

MVP keeps memory deliberately simple: short-term context is held in-process,
user profile and durable facts live in PostgreSQL, and hot tool results can
be cached in Redis. This module only defines the interface plus an in-memory
default implementation.
"""

from __future__ import annotations

from typing import Any, Protocol


class AgentMemory(Protocol):
    """Minimal memory contract for the MVP runtime."""

    def remember(self, key: str, value: Any) -> None: ...

    def recall(self, key: str) -> Any | None: ...

    def forget(self, key: str) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...


class InMemoryAgentMemory:
    """Default short-term memory used by the smoke workflow."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def remember(self, key: str, value: Any) -> None:
        self._store[key] = value

    def recall(self, key: str) -> Any | None:
        return self._store.get(key)

    def forget(self, key: str) -> None:
        self._store.pop(key, None)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._store)
