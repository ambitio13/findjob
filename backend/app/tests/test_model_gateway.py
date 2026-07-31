"""Model gateway tests using the fake provider (no network, no API key)."""

from __future__ import annotations

import pytest

from app.models_gateway.base import ChatMessage, ChatRequest
from app.models_gateway.factory import get_model_gateway
from app.models_gateway.fake import FakeModelGateway


@pytest.mark.asyncio
async def test_fake_gateway_chat() -> None:
    gw = FakeModelGateway()
    resp = await gw.chat(ChatRequest(messages=[ChatMessage(role="user", content="hello")]))
    assert resp.provider == "fake"
    assert "hello" in resp.content


def test_factory_returns_fake_when_no_key(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("MODEL_PROVIDER", "auto")
    from app.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    gw = get_model_gateway()
    assert gw.provider_name == "fake"
