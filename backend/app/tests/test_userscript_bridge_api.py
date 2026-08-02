"""Tests for the userscript bridge HTTP endpoints.

These tests verify the four bridge endpoints (status, next-instruction, result,
heartbeat) without any real userscript. The channel singleton is reset between
tests so state does not leak.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
from app.platforms.boss.userscript_channel import (
    get_channel,
    make_instruction,
    reset_channel,
)


def _client() -> TestClient:
    return TestClient(create_app())


def _reset() -> None:
    reset_channel()


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


def test_status_initially_disconnected() -> None:
    _reset()
    with _client() as client:
        resp = client.get("/api/v1/userscript-bridge/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["last_heartbeat"] is None
    assert body["active_application_id"] is None


def test_status_connected_after_heartbeat() -> None:
    _reset()
    with _client() as client:
        client.post("/api/v1/userscript-bridge/heartbeat", json={})
        resp = client.get("/api/v1/userscript-bridge/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["last_heartbeat"] is not None


# ---------------------------------------------------------------------------
# POST /heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_returns_ack() -> None:
    _reset()
    with _client() as client:
        resp = client.post(
            "/api/v1/userscript-bridge/heartbeat",
            json={"page_url_hash": "sha256:abc", "page_title": "BOSS"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_heartbeat_empty_body_ok() -> None:
    _reset()
    with _client() as client:
        resp = client.post("/api/v1/userscript-bridge/heartbeat", json={})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /next-instruction
# ---------------------------------------------------------------------------


def test_next_instruction_returns_204_when_empty() -> None:
    _reset()
    with _client() as client:
        # The long-poll timeout is 5s; we don't want to wait that long in tests.
        # Patch the poll timeout to 0.1s.
        import app.platforms.boss.userscript_channel as mod

        original = mod.INSTRUCTION_POLL_TIMEOUT_S
        mod.INSTRUCTION_POLL_TIMEOUT_S = 0.1
        try:
            resp = client.get("/api/v1/userscript-bridge/next-instruction")
        finally:
            mod.INSTRUCTION_POLL_TIMEOUT_S = original
    assert resp.status_code == 204


def test_next_instruction_returns_instruction_when_queued() -> None:
    _reset()
    ch = get_channel()
    instruction = make_instruction(
        "fill",
        selector_kind="placeholder",
        selector_value="请输入你要发送的内容",
        fill_value="你好",
    )
    asyncio.run(ch._queue.put(instruction))
    with _client() as client:
        resp = client.get("/api/v1/userscript-bridge/next-instruction")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instruction_id"] == instruction.instruction_id
    assert body["op"] == "fill"
    assert body["selector_kind"] == "placeholder"
    assert body["selector_value"] == "请输入你要发送的内容"
    assert body["fill_value"] == "你好"


# ---------------------------------------------------------------------------
# POST /result
# ---------------------------------------------------------------------------


def test_post_result_stores_and_returns_ack() -> None:
    _reset()
    ch = get_channel()
    instruction = make_instruction("count", selector_kind="css", selector_value=".x")
    asyncio.run(ch._queue.put(instruction))

    with _client() as client:
        # Take the instruction so it's "completed" on the userscript side.
        client.get("/api/v1/userscript-bridge/next-instruction")
        # Post back the result.
        resp = client.post(
            "/api/v1/userscript-bridge/result",
            json={
                "instruction_id": instruction.instruction_id,
                "success": True,
                "count": 3,
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify the result was stored in the channel.
    result = ch._results.get(instruction.instruction_id)
    assert result is not None
    assert result.count == 3
    assert result.success is True


def test_post_result_sanitizes_url() -> None:
    """The endpoint must hash raw URLs even if the userscript sends one."""
    _reset()
    _reset()
    ch = get_channel()
    instruction = make_instruction("read_url")
    asyncio.run(ch._queue.put(instruction))

    with _client() as client:
        client.get("/api/v1/userscript-bridge/next-instruction")
        resp = client.post(
            "/api/v1/userscript-bridge/result",
            json={
                "instruction_id": instruction.instruction_id,
                "success": True,
                "url": "https://www.zhipin.com/job/123?token=secret",
            },
        )
    assert resp.status_code == 200
    result = ch._results.get(instruction.instruction_id)
    assert result is not None
    assert result.url is not None
    assert result.url.startswith("sha256:")
    assert "zhipin" not in result.url
    assert "secret" not in result.url


def test_post_result_sanitizes_text() -> None:
    """The endpoint must strip secrets from text fields."""
    _reset()
    ch = get_channel()
    instruction = make_instruction("read_title")
    asyncio.run(ch._queue.put(instruction))

    with _client() as client:
        client.get("/api/v1/userscript-bridge/next-instruction")
        resp = client.post(
            "/api/v1/userscript-bridge/result",
            json={
                "instruction_id": instruction.instruction_id,
                "success": True,
                "text": "Page title with token=abc123secret",
            },
        )
    assert resp.status_code == 200
    result = ch._results.get(instruction.instruction_id)
    assert result is not None
    assert result.text is not None
    assert "abc123secret" not in result.text


# ---------------------------------------------------------------------------
# Integration: heartbeat → status → next-instruction → result
# ---------------------------------------------------------------------------


def test_full_round_trip() -> None:
    """Heartbeat, queue instruction, take it, post result."""
    _reset()
    ch = get_channel()
    instruction = make_instruction(
        "check_visible", selector_kind="css", selector_value=".login-wrap"
    )

    with _client() as client:
        # 1. Heartbeat.
        client.post("/api/v1/userscript-bridge/heartbeat", json={})
        # 2. Status shows connected.
        status = client.get("/api/v1/userscript-bridge/status").json()
        assert status["connected"] is True

        # 3. Queue an instruction (simulating the adapter enqueuing).
        asyncio.run(ch._queue.put(instruction))

        # 4. Userscript takes it.
        taken = client.get("/api/v1/userscript-bridge/next-instruction").json()
        assert taken["instruction_id"] == instruction.instruction_id

        # 5. Userscript posts result.
        client.post(
            "/api/v1/userscript-bridge/result",
            json={
                "instruction_id": instruction.instruction_id,
                "success": True,
                "visible": True,
            },
        )

    # 6. Result is in the channel.
    result = ch._results.get(instruction.instruction_id)
    assert result is not None
    assert result.visible is True
