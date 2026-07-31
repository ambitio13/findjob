"""DeepSeek (OpenAI-compatible) model gateway provider.

Uses ``httpx`` to call the OpenAI-compatible chat completions endpoint. This is
the ONLY module permitted to perform HTTP model calls. All other code goes
through :class:`app.models_gateway.base.ModelGateway`.
"""

from __future__ import annotations

import json
from typing import Generic, TypeVar

import httpx

from app.core.logging import get_logger
from app.models_gateway.base import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    ModelGateway,
    StructuredRequest,
)

T = TypeVar("T")

_log = get_logger(__name__)


class DeepSeekModelGateway(ModelGateway, Generic[T]):
    provider_name = "deepseek"

    def __init__(self, base_url: str, api_key: str, default_model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model

    async def chat(self, request: ChatRequest) -> ChatResponse:
        model = request.model or self._default_model
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        start = self._now_ms()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=self._auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        latency = self._now_ms() - start

        content = data["choices"][0]["message"]["content"]
        usage_raw = data.get("usage") or {}
        return ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=latency,
            usage=ChatUsage(
                prompt_tokens=usage_raw.get("prompt_tokens"),
                completion_tokens=usage_raw.get("completion_tokens"),
                total_tokens=usage_raw.get("total_tokens"),
            ),
            raw=data,
        )

    async def structured(self, request: StructuredRequest[T]) -> T:
        # Request JSON output and parse into the target model. DeepSeek's
        # OpenAI-compatible API supports ``response_format`` json_object.
        model = request.model or self._default_model
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
        }
        start = self._now_ms()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers=self._auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        latency = self._now_ms() - start
        _log.info(
            "model.structured_call",
            provider=self.provider_name,
            model=model,
            latency_ms=latency,
            request_id=request.request_id,
        )

        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return request.response_model.model_validate(parsed)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
