"""Fake model gateway for tests and no-key local development.

Returns deterministic, schema-valid responses without any network access.
This is the default provider when ``MODEL_API_KEY`` is empty.
"""

from __future__ import annotations

from app.models_gateway.base import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    ModelGateway,
    StructuredRequest,
)


class FakeModelGateway(ModelGateway):
    provider_name = "fake"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        joined = " | ".join(m.content for m in request.messages)
        content = f"[fake-model] echo: {joined[:200]}"
        usage = ChatUsage(
            prompt_tokens=len(joined),
            completion_tokens=len(content),
            total_tokens=len(joined) + len(content),
        )
        return ChatResponse(
            content=content,
            model=request.model or "fake-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=1,
            usage=usage,
            raw={"echo": True},
        )

    async def structured(self, request: StructuredRequest) -> object:
        # Emit a minimal valid instance of the requested model. Fields with
        # defaults stay default; required fields are filled heuristically.
        sample = _sample_instance(request.response_model)
        return sample


def _sample_instance(model_cls: type) -> object:
    """Best-effort construction of a Pydantic model with sensible defaults."""
    try:
        return model_cls.model_validate_json("{}")
    except Exception:
        pass

    # Fall back to constructing from JSON-like defaults derived from field types.
    data: dict[str, object] = {}
    for name, field in model_cls.model_fields.items():
        if field.is_required():
            data[name] = _default_for(field)
    try:
        return model_cls.model_validate(data)
    except Exception:
        # Last resort: return a plain dict so callers can still introspect.
        return data  # type: ignore[return-value]


def _default_for(field) -> object:  # type: ignore[no-untyped-def]
    ann = field.annotation
    if ann is str or ann == "str":
        return ""
    if ann is int or ann == "int":
        return 0
    if ann is float or ann == "float":
        return 0.0
    if ann is bool or ann == "bool":
        return False
    return {}
