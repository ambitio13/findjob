"""Tool registry skeleton.

Every tool declares a name, an input schema, and an async ``run`` method.
Tools must validate input, enforce permissions, record results, and support
idempotency keys for state-changing calls. The MVP registry only holds
metadata; concrete tools arrive in later tasks.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ToolInput(BaseModel):
    """Base input schema. Tools subclass this to declare their contract."""


class Tool(Protocol):
    name: str
    schema_version: str

    async def run(self, request: ToolInput) -> dict[str, Any]: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools.keys())


def default_registry() -> ToolRegistry:
    """Return an empty default registry for the MVP skeleton."""
    return ToolRegistry()
