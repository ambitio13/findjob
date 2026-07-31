"""Model gateway factory.

Resolves the active provider from settings. When the API key is missing the
fake provider is used so local tests never require network access.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.models_gateway.base import ModelGateway
from app.models_gateway.deepseek import DeepSeekModelGateway
from app.models_gateway.fake import FakeModelGateway


def get_model_gateway() -> ModelGateway:
    settings = get_settings()
    provider = settings.effective_provider

    if provider == "fake":
        return FakeModelGateway()
    # Both "deepseek" and "openai" use the OpenAI-compatible shape.
    return DeepSeekModelGateway(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        default_model=settings.model_default_model,
    )
