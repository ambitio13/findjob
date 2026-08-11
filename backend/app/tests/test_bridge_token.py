"""Bridge channel-token guard tests (Phase 0 guardrail).

The userscript bridge data plane (``next-instruction`` / ``result`` /
``heartbeat`` / ``probe``) is guarded by a shared channel token when
configured; ``GET /status`` stays open for the frontend connection
indicator. In prod an unconfigured token refuses the data plane entirely.

Settings are cached, so each test toggles env vars and clears the cache
(established pattern from ``test_boss_real_adapter.py``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings

_HEARTBEAT = "/api/v1/userscript-bridge/heartbeat"
_BODY = {"page_id": "page_test", "page_url_hash": "sha256:abc", "page_title": "BOSS"}


@pytest.fixture()
def bridge_token_env(monkeypatch: pytest.MonkeyPatch):
    """Configure a channel token for the duration of one test."""
    monkeypatch.setenv("BOSS_BRIDGE_CHANNEL_TOKEN", "chan-token-123")
    get_settings.cache_clear()
    yield "chan-token-123"
    monkeypatch.delenv("BOSS_BRIDGE_CHANNEL_TOKEN", raising=False)
    get_settings.cache_clear()


def test_heartbeat_rejected_without_token(client: TestClient, bridge_token_env: str) -> None:
    resp = client.post(_HEARTBEAT, json=_BODY)
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "bridge_token_invalid"


def test_heartbeat_rejected_with_wrong_token(client: TestClient, bridge_token_env: str) -> None:
    resp = client.post(_HEARTBEAT, json=_BODY, headers={"X-Bridge-Token": "wrong"})
    assert resp.status_code == 401


def test_heartbeat_accepted_with_matching_token(client: TestClient, bridge_token_env: str) -> None:
    resp = client.post(_HEARTBEAT, json=_BODY, headers={"X-Bridge-Token": bridge_token_env})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_status_stays_open_for_frontend(client: TestClient, bridge_token_env: str) -> None:
    # The frontend connection indicator must work without knowing the token.
    resp = client.get("/api/v1/userscript-bridge/status")
    assert resp.status_code == 200


def test_next_instruction_requires_token(client: TestClient, bridge_token_env: str) -> None:
    resp = client.get("/api/v1/userscript-bridge/next-instruction")
    assert resp.status_code == 401


def test_legacy_open_behaviour_without_token(client: TestClient) -> None:
    # Test/local env without a configured token keeps the legacy open data
    # plane so existing userscripts and tests work unchanged.
    get_settings.cache_clear()
    resp = client.post(_HEARTBEAT, json=_BODY)
    assert resp.status_code == 200, resp.text


def test_prod_refuses_open_data_plane(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("BOSS_BRIDGE_CHANNEL_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        resp = client.post(_HEARTBEAT, json=_BODY)
        assert resp.status_code == 503
        assert resp.json()["detail"]["reason"] == "bridge_channel_token_not_configured"
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()
