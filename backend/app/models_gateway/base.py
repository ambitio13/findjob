"""Model gateway request/response schemas and provider-neutral interface.

Business code depends only on :class:`ModelGateway`. Provider SDKs (DeepSeek /
OpenAI-compatible) live behind concrete providers in this package. No other
module may import a provider SDK directly.
"""

from __future__ import annotations

import time
import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex}")


class ChatUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatResponse(BaseModel):
    content: str
    model: str
    provider: str
    request_id: str
    latency_ms: int
    usage: ChatUsage | None = None
    raw: dict | None = None


class StructuredRequest(BaseModel, Generic[T]):
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float = 0.1
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex}")
    response_model: type[T]


class ModelGateway:
    """Provider-neutral model gateway interface.

    Concrete implementations live in :mod:`app.models_gateway.deepseek` and
    :mod:`app.models_gateway.fake`.
    """

    provider_name: str = "base"

    async def chat(self, request: ChatRequest) -> ChatResponse:  # pragma: no cover - interface
        raise NotImplementedError

    async def structured(self, request: StructuredRequest[T]) -> T:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def _now_ms() -> int:
        return int((time.perf_counter()) * 1000)
