"""Model gateway factory.

Resolves the active provider from settings. When the API key is missing the
fake provider is used so local tests never require network access. The
returned gateway is always wrapped in :class:`BudgetedModelGateway` so the
daily call budget is enforced on every LLM invocation regardless of whether
the caller is the HTTP path or the worker path — this is the single
resolution point for both.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.models_gateway.base import ModelGateway
from app.models_gateway.budget import BudgetedModelGateway
from app.models_gateway.deepseek import DeepSeekModelGateway
from app.models_gateway.fake import FakeModelGateway


def get_model_gateway() -> ModelGateway:
    settings = get_settings()
    provider = settings.effective_provider

    if provider == "fake":
        inner: ModelGateway = FakeModelGateway()
    else:
        # Both "deepseek" and "openai" use the OpenAI-compatible shape.
        inner = DeepSeekModelGateway(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            default_model=settings.model_default_model,
        )

    # Wrap with the daily budget circuit breaker. This is the cost defense
    # line; RateLimitMiddleware is only per-minute request-experience protection.
    return BudgetedModelGateway(inner)
