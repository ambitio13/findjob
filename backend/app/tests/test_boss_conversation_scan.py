"""Conversation status scan tests (Phase 1 read-only feedback loop).

Covers:

- the scan endpoint refuses to run without a connected userscript;
- a connected scan returns desensitized entries (hashed keys + statuses);
- the bridge ``POST /result`` accepts and caps the ``conversations`` field;
- the adapter surfaces ``result.conversations`` and maps failure to None.

The userscript DOM side cannot run here; the channel instruction path is
stubbed exactly like the existing adapter tests do.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.models.models import UserProfile
from app.db.session import SessionLocal
from app.platforms.boss.userscript_adapter import UserscriptBossPage
from app.platforms.boss.userscript_channel import (
    InstructionResult,
    get_channel,
    reset_channel,
)

_USER = "scan_user"
_SCAN = "/api/v1/boss/conversations/scan"


def _seed_user() -> None:
    with SessionLocal() as db:
        db.add(UserProfile(id=_USER, display_name="扫描用户"))
        db.commit()


def _headers() -> dict[str, str]:
    return {"X-User-Id": _USER}


def _connect_channel() -> None:
    reset_channel()
    ch = get_channel()
    ch.heartbeat(page_id="tab-test", page_url_hash="sha256:test")


@pytest.fixture(autouse=True)
def _clean_channel():
    reset_channel()
    yield
    reset_channel()


# ---------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------


def test_scan_requires_connected_bridge(client: TestClient) -> None:
    _seed_user()
    resp = client.post(_SCAN, headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["scan_status"] == "bridge_not_connected"
    assert body["conversations"] == []


def test_scan_returns_desensitized_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_user()
    _connect_channel()

    async def fake_put_instruction(instruction):
        assert instruction.op == "scan_conversations"
        return InstructionResult(
            instruction_id=instruction.instruction_id,
            success=True,
            conversations=[
                {"conversation_key_hash": "sha256:abc", "status": "replied"},
                {"conversation_key_hash": "sha256:def", "status": "read"},
            ],
            page_id=instruction.page_id,
        )

    monkeypatch.setattr(get_channel(), "put_instruction", fake_put_instruction)

    resp = client.post(_SCAN, headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scan_status"] == "ok"
    assert body["conversations"] == [
        {"conversation_key_hash": "sha256:abc", "status": "replied"},
        {"conversation_key_hash": "sha256:def", "status": "read"},
    ]


def test_scan_failure_maps_to_scan_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_user()
    _connect_channel()

    async def fake_put_instruction(instruction):
        return InstructionResult(
            instruction_id=instruction.instruction_id,
            success=False,
            error="chat_list_not_found",
            page_id=instruction.page_id,
        )

    monkeypatch.setattr(get_channel(), "put_instruction", fake_put_instruction)

    resp = client.post(_SCAN, headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["scan_status"] == "scan_failed"


# ---------------------------------------------------------------------------
# Adapter unit behaviour
# ---------------------------------------------------------------------------


async def test_adapter_returns_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect_channel()
    ch = get_channel()

    async def fake_put_instruction(instruction):
        return InstructionResult(
            instruction_id=instruction.instruction_id,
            success=True,
            conversations=[{"conversation_key_hash": "sha256:x", "status": "unread"}],
            page_id=instruction.page_id,
        )

    monkeypatch.setattr(ch, "put_instruction", fake_put_instruction)
    page = UserscriptBossPage(ch)
    entries = await page.scan_conversation_statuses()
    assert entries == [{"conversation_key_hash": "sha256:x", "status": "unread"}]


async def test_adapter_returns_none_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect_channel()
    ch = get_channel()

    async def fake_put_instruction(instruction):
        return InstructionResult(
            instruction_id=instruction.instruction_id, success=False
        )

    monkeypatch.setattr(ch, "put_instruction", fake_put_instruction)
    page = UserscriptBossPage(ch)
    assert await page.scan_conversation_statuses() is None


# ---------------------------------------------------------------------------
# Bridge result wire-format: conversations field accepted + capped
# ---------------------------------------------------------------------------


def test_result_accepts_conversations(client: TestClient) -> None:
    _connect_channel()
    body = {
        "instruction_id": "ins_test",
        "success": True,
        "conversations": [
            {"conversation_key_hash": "sha256:abc", "status": "replied"},
        ],
    }
    resp = client.post("/api/v1/userscript-bridge/result", json=body)
    assert resp.status_code == 200


def test_result_caps_conversations_at_50(client: TestClient) -> None:
    _connect_channel()
    body = {
        "instruction_id": "ins_test",
        "success": True,
        "conversations": [
            {"conversation_key_hash": f"sha256:{i:04d}", "status": "unknown"}
            for i in range(51)
        ],
    }
    resp = client.post("/api/v1/userscript-bridge/result", json=body)
    assert resp.status_code == 422


def test_result_rejects_invalid_status_value(client: TestClient) -> None:
    _connect_channel()
    body = {
        "instruction_id": "ins_test",
        "success": True,
        "conversations": [
            {"conversation_key_hash": "sha256:abc", "status": "chat_transcript"}
        ],
    }
    resp = client.post("/api/v1/userscript-bridge/result", json=body)
    assert resp.status_code == 422
