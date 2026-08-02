"""Tests for the in-memory userscript bridge channel.

These tests verify the queue mechanics, heartbeat liveness, single-active
invariant, bounded timeouts, page binding, and clear() behavior. No HTTP or
Playwright is involved — the channel is pure in-memory asyncio.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.platforms.boss.userscript_channel import (
    CONNECTION_TIMEOUT_S,
    Instruction,
    InstructionResult,
    UserscriptChannel,
    get_channel,
    make_instruction,
    reset_channel,
    sanitize_result_error,
    sanitize_result_text,
    sanitize_result_url,
)

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_channel_returns_singleton() -> None:
    reset_channel()
    ch1 = get_channel()
    ch2 = get_channel()
    assert ch1 is ch2


def test_reset_channel_creates_fresh_instance() -> None:
    reset_channel()
    ch1 = get_channel()
    reset_channel()
    ch2 = get_channel()
    assert ch1 is not ch2


# ---------------------------------------------------------------------------
# Heartbeat / connection status
# ---------------------------------------------------------------------------


def test_initially_disconnected() -> None:
    ch = UserscriptChannel()
    assert not ch.is_connected()
    assert ch.last_heartbeat is None


def test_heartbeat_makes_connected() -> None:
    ch = UserscriptChannel()
    ch.heartbeat()
    assert ch.is_connected()
    assert ch.last_heartbeat is not None


def test_heartbeat_with_page_metadata() -> None:
    """Heartbeat with page_id stores active page metadata."""
    ch = UserscriptChannel()
    ch.heartbeat(
        page_id="page-abc",
        page_url_hash="sha256:deadbeef",
        page_title="BOSS Job",
    )
    assert ch.is_connected()
    assert ch.active_page is not None
    assert ch.active_page.page_id == "page-abc"
    assert ch.active_page.page_url_hash == "sha256:deadbeef"
    assert ch.active_page.page_title == "BOSS Job"


def test_heartbeat_without_page_id_keeps_old_page() -> None:
    """Old-style heartbeat (no page_id) retains previous page metadata."""
    ch = UserscriptChannel()
    ch.heartbeat(page_id="page-1", page_url_hash="sha256:aaa")
    ch.heartbeat()  # no page_id — backward compatible
    assert ch.active_page is not None
    assert ch.active_page.page_id == "page-1"


def test_stale_heartbeat_makes_disconnected() -> None:
    ch = UserscriptChannel()
    ch._last_heartbeat = datetime.now(UTC) - timedelta(seconds=CONNECTION_TIMEOUT_S + 1)
    assert not ch.is_connected()


def test_recent_heartbeat_stays_connected() -> None:
    ch = UserscriptChannel()
    ch._last_heartbeat = datetime.now(UTC) - timedelta(seconds=CONNECTION_TIMEOUT_S - 1)
    assert ch.is_connected()


# ---------------------------------------------------------------------------
# Instruction / result exchange
# ---------------------------------------------------------------------------


async def test_put_instruction_and_put_result() -> None:
    """The adapter sends an instruction; a consumer posts back the result."""
    ch = UserscriptChannel()
    instruction = make_instruction("check_visible", selector_kind="css", selector_value=".x")

    # Run the producer (put_instruction) and consumer (take_instruction +
    # put_result) concurrently.
    async def producer() -> InstructionResult:
        return await ch.put_instruction(instruction)

    async def consumer() -> None:
        taken = await ch.take_instruction()
        assert taken is not None
        assert taken.instruction_id == instruction.instruction_id
        ch.put_result(
            InstructionResult(
                instruction_id=taken.instruction_id,
                success=True,
                visible=True,
            )
        )

    result, _ = await asyncio.gather(producer(), consumer())
    assert result.success is True
    assert result.visible is True


async def test_take_instruction_returns_none_on_timeout() -> None:
    ch = UserscriptChannel()
    # No instruction in queue → should return None after the poll timeout.
    # Use a short timeout by monkey-patching the poll duration.
    import app.platforms.boss.userscript_channel as mod

    original = mod.INSTRUCTION_POLL_TIMEOUT_S
    mod.INSTRUCTION_POLL_TIMEOUT_S = 0.1
    try:
        result = await ch.take_instruction()
    finally:
        mod.INSTRUCTION_POLL_TIMEOUT_S = original
    assert result is None


async def test_result_timeout_returns_failure() -> None:
    """If no result arrives, put_instruction returns a failure result."""
    ch = UserscriptChannel()
    instruction = make_instruction("count", selector_kind="css", selector_value=".x")

    # Shorten the result timeout so the test doesn't wait 15s.
    import app.platforms.boss.userscript_channel as mod

    original = mod.RESULT_TIMEOUT_S
    mod.RESULT_TIMEOUT_S = 0.2
    try:
        result = await ch.put_instruction(instruction)
    finally:
        mod.RESULT_TIMEOUT_S = original

    assert result.success is False
    assert result.error is not None


async def test_multiple_instructions_fifo_order() -> None:
    ch = UserscriptChannel()
    ins1 = make_instruction("count", selector_kind="css", selector_value=".a")
    ins2 = make_instruction("count", selector_kind="css", selector_value=".b")

    await ch._queue.put(ins1)
    await ch._queue.put(ins2)

    taken1 = await ch.take_instruction()
    taken2 = await ch.take_instruction()
    assert taken1 is not None
    assert taken2 is not None
    assert taken1.instruction_id == ins1.instruction_id
    assert taken2.instruction_id == ins2.instruction_id


# ---------------------------------------------------------------------------
# Single-active application invariant
# ---------------------------------------------------------------------------


async def test_set_active_application_succeeds_when_disconnected() -> None:
    ch = UserscriptChannel()
    await ch.set_active_application("app-1")
    assert ch.active_application_id == "app-1"


async def test_set_same_application_is_idempotent() -> None:
    ch = UserscriptChannel()
    await ch.set_active_application("app-1")
    await ch.set_active_application("app-1")  # no error
    assert ch.active_application_id == "app-1"


async def test_set_different_application_raises_when_connected() -> None:
    ch = UserscriptChannel()
    ch.heartbeat()  # connected
    await ch.set_active_application("app-1")
    with pytest.raises(RuntimeError, match="already active"):
        await ch.set_active_application("app-2")


async def test_set_different_application_allows_when_stale() -> None:
    """If the previous application is stale (disconnected), allow overwrite."""
    ch = UserscriptChannel()
    await ch.set_active_application("app-1")
    # No heartbeat → disconnected → stale application can be overwritten.
    await ch.set_active_application("app-2")
    assert ch.active_application_id == "app-2"


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------


async def test_clear_empties_queue_and_results() -> None:
    ch = UserscriptChannel()
    ins = make_instruction("count", selector_kind="css", selector_value=".x")
    await ch._queue.put(ins)
    ch._results["ins_1"] = InstructionResult(instruction_id="ins_1", success=True)
    ch._active_application_id = "app-1"
    ch._pending_instruction = ins

    ch.clear()

    assert ch._queue.empty()
    assert ch._results == {}
    assert ch.active_application_id is None
    assert ch._pending_instruction is None


async def test_clear_does_not_clear_active_page() -> None:
    """clear() retains active page metadata; only clear_stale_page() clears it."""
    ch = UserscriptChannel()
    ch.heartbeat(page_id="page-1", page_url_hash="sha256:abc")
    ch.clear()
    assert ch.active_page is not None
    assert ch.active_page.page_id == "page-1"


def test_clear_stale_page_clears_active_page() -> None:
    """clear_stale_page() explicitly clears active page metadata."""
    ch = UserscriptChannel()
    ch.heartbeat(page_id="page-1", page_url_hash="sha256:abc")
    ch.clear_stale_page()
    assert ch.active_page is None


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------


def test_sanitize_result_text_strips_secrets() -> None:
    result = sanitize_result_text("Hello bearer abc123.world")
    assert result is not None
    assert "abc123.world" not in result
    assert "<redacted>" in result


def test_sanitize_result_text_none_returns_none() -> None:
    assert sanitize_result_text(None) is None
    assert sanitize_result_text("") is None


def test_sanitize_result_url_hashes() -> None:
    result = sanitize_result_url("https://www.zhipin.com/job/123?token=secret")
    assert result is not None
    assert result.startswith("sha256:")
    assert "zhipin" not in result
    assert "secret" not in result


def test_sanitize_result_url_none_returns_none() -> None:
    assert sanitize_result_url(None) is None


def test_sanitize_result_error_strips_paths() -> None:
    result = sanitize_result_error("/home/user/.boss-debug-chrome/cookies.json")
    assert result is not None
    assert "cookies" not in result or result == "<redacted-path>"


def test_make_instruction_generates_unique_ids() -> None:
    ins1 = make_instruction("count")
    ins2 = make_instruction("count")
    assert ins1.instruction_id != ins2.instruction_id


def test_make_instruction_accepts_page_binding() -> None:
    """make_instruction can carry page_id and expected_url_hash."""
    ins = make_instruction(
        "fill",
        selector_kind="css",
        selector_value=".msg",
        fill_value="hello",
        page_id="page-1",
        expected_url_hash="sha256:abcd",
    )
    assert ins.page_id == "page-1"
    assert ins.expected_url_hash == "sha256:abcd"


# ---------------------------------------------------------------------------
# Page binding: multiple page IDs cannot consume each other's instructions
# ---------------------------------------------------------------------------


async def test_page_id_mismatch_rejects_result() -> None:
    """A result from a different page_id must be rejected by put_result.

    This is the core safety guarantee: if instruction A was dispatched for
    page_id='tab-1', a result posted with page_id='tab-2' must not satisfy it.
    The result is rejected and the instruction times out.
    """
    ch = UserscriptChannel()
    instruction = make_instruction("count", selector_kind="css", selector_value=".x")
    instruction = Instruction(
        instruction_id=instruction.instruction_id,
        op=instruction.op,
        selector_kind=instruction.selector_kind,
        selector_value=instruction.selector_value,
        selector_name=instruction.selector_name,
        fill_value=instruction.fill_value,
        page_id="tab-1",
    )

    # Shorten the result timeout so the test doesn't wait 15s.
    import app.platforms.boss.userscript_channel as mod

    original_timeout = mod.RESULT_TIMEOUT_S
    mod.RESULT_TIMEOUT_S = 0.3

    async def producer() -> InstructionResult:
        return await ch.put_instruction(instruction)

    async def wrong_consumer() -> None:
        taken = await ch.take_instruction()
        assert taken is not None
        # Wrong tab posts a result.
        accepted = ch.put_result(
            InstructionResult(
                instruction_id=taken.instruction_id,
                success=True,
                count=42,
                page_id="tab-2",  # wrong page!
            )
        )
        assert accepted is False

    try:
        result, _ = await asyncio.gather(producer(), wrong_consumer())
    finally:
        mod.RESULT_TIMEOUT_S = original_timeout

    # The result was rejected → timeout failure.
    assert result.success is False
    assert result.error is not None


async def test_page_id_match_accepts_result() -> None:
    """A result with matching page_id is accepted normally."""
    ch = UserscriptChannel()
    instruction = make_instruction("count", selector_kind="css", selector_value=".x")
    instruction = Instruction(
        instruction_id=instruction.instruction_id,
        op=instruction.op,
        selector_kind=instruction.selector_kind,
        selector_value=instruction.selector_value,
        selector_name=instruction.selector_name,
        fill_value=instruction.fill_value,
        page_id="tab-1",
    )

    async def producer() -> InstructionResult:
        return await ch.put_instruction(instruction)

    async def consumer() -> None:
        taken = await ch.take_instruction()
        assert taken is not None
        accepted = ch.put_result(
            InstructionResult(
                instruction_id=taken.instruction_id,
                success=True,
                count=5,
                page_id="tab-1",  # correct page
            )
        )
        assert accepted is True

    result, _ = await asyncio.gather(producer(), consumer())
    assert result.success is True
    assert result.count == 5


async def test_result_without_page_id_accepted_when_instruction_has_page_id() -> None:
    """A result with page_id=None is accepted (backward compatible)."""
    ch = UserscriptChannel()
    instruction = make_instruction("count", selector_kind="css", selector_value=".x")
    instruction = Instruction(
        instruction_id=instruction.instruction_id,
        op=instruction.op,
        selector_kind=instruction.selector_kind,
        selector_value=instruction.selector_value,
        page_id="tab-1",
    )

    async def producer() -> InstructionResult:
        return await ch.put_instruction(instruction)

    async def consumer() -> None:
        taken = await ch.take_instruction()
        assert taken is not None
        # No page_id on result — accepted (old userscript).
        accepted = ch.put_result(
            InstructionResult(
                instruction_id=taken.instruction_id,
                success=True,
                count=3,
                page_id=None,
            )
        )
        assert accepted is True

    result, _ = await asyncio.gather(producer(), consumer())
    assert result.success is True
    assert result.count == 3


# ---------------------------------------------------------------------------
# Page binding: auto-bind from active page
# ---------------------------------------------------------------------------


async def test_put_instruction_auto_binds_page_id() -> None:
    """When the instruction has no page_id, it is bound from the active page."""
    ch = UserscriptChannel()
    ch.heartbeat(
        page_id="auto-page",
        page_url_hash="sha256:auto",
        page_title="Auto",
    )
    instruction = make_instruction("count", selector_kind="css", selector_value=".x")

    # Capture the instruction that goes into the queue.
    import app.platforms.boss.userscript_channel as mod

    original_timeout = mod.RESULT_TIMEOUT_S
    mod.RESULT_TIMEOUT_S = 0.2

    async def consumer() -> Instruction | None:
        taken = await ch.take_instruction()
        return taken

    try:
        consumer_task = asyncio.create_task(consumer())
        await ch.put_instruction(instruction)
        taken = await consumer_task
    finally:
        mod.RESULT_TIMEOUT_S = original_timeout

    assert taken is not None
    assert taken.page_id == "auto-page"
    assert taken.expected_url_hash == "sha256:auto"


# ---------------------------------------------------------------------------
# Page binding: heartbeat timeout clears active page safety state
# ---------------------------------------------------------------------------


def test_stale_heartbeat_clears_active_page() -> None:
    """When the heartbeat goes stale, clear_stale_page() clears the page metadata.

    This simulates the safety mechanism: after a disconnection, the stale page
    binding must not authorize instructions to a reconnected-but-different tab.
    """
    ch = UserscriptChannel()
    ch.heartbeat(page_id="page-1", page_url_hash="sha256:abc")
    assert ch.active_page is not None
    assert ch.active_page.page_id == "page-1"

    # Simulate the heartbeat going stale.
    ch._last_heartbeat = datetime.now(UTC) - timedelta(seconds=CONNECTION_TIMEOUT_S + 1)
    assert not ch.is_connected()

    # The safety mechanism clears the stale page.
    ch.clear_stale_page()
    assert ch.active_page is None
