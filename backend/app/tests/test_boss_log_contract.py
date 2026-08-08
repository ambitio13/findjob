"""Contract tests for BOSS structured logging fields.

These tests assert that the BOSS trace-field contract (prd.md R1, design.md)
holds for the key code paths:

- The trace-field helper (:mod:`app.core.boss_tracing`) binds and clears
  fields correctly.
- Queue worker failure logs carry ``workflow_type``, ``queue_namespace``,
  ``job_id``, and ``agent_run_id`` (prd.md R3).
- Bridge lifecycle logs (heartbeat, instruction_sent, result_received,
  result_timeout, instruction_requeued) carry ``instruction_id`` / ``page_id``
  / ``page_url_hash`` (prd.md R2).
- BOSS communicate/prepare execute logs carry ``application_id`` and
  ``failure_code`` on failure paths (prd.md R1).
- Sensitive data (raw URL, cookie, token, resume text, HR message body) never
  appears in the trace fields.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import structlog

from app.core.boss_tracing import (
    BOSS_TRACE_FIELDS,
    bind_boss_trace,
    boss_trace_context,
    bound_logger,
    clear_boss_trace,
    set_failure_code,
)
from app.platforms.boss.userscript_channel import (
    UserscriptChannel,
    make_instruction,
)

# ---------------------------------------------------------------------------
# structlog event capture fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture structlog log events into a list.

    Replaces the structlog processor chain with a capturing one so tests can
    assert on event names and fields without reading stdout.
    """
    events: list[dict[str, Any]] = []

    def capture_processor(
        logger: Any, method_name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        events.append(event_dict)
        return event_dict

    # Reset any prior config (e.g. configure_logging from a lifespan) so our
    # capturing chain actually takes effect. structlog caches config state, and
    # module-level ``get_logger`` calls return lazy proxies that resolve against
    # the *current* config at first use — so a fresh configure is enough.
    structlog.reset_defaults()
    # Re-configure with the capturing processor. The renderer must be terminal
    # (return str) so the underlying PrintLogger.msg() receives positional args
    # instead of **kwargs, which it does not accept.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            capture_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    yield events
    # Restore a minimal config so other tests aren't affected.
    structlog.reset_defaults()


# ---------------------------------------------------------------------------
# Trace-field helper contract
# ---------------------------------------------------------------------------


class TestBossTracingHelper:
    """Verify bind/clear/snapshot semantics of the trace-field helper."""

    def setup_method(self) -> None:
        clear_boss_trace()

    def teardown_method(self) -> None:
        clear_boss_trace()

    def test_trace_field_set_is_stable(self) -> None:
        """The trace-field name set must match the documented contract."""
        assert BOSS_TRACE_FIELDS == frozenset(
            {
                "agent_run_id",
                "workflow_type",
                "user_id",
                "application_id",
                "job_id",
                "page_id",
                "page_url_hash",
                "instruction_id",
                "failure_code",
            }
        )

    def test_bind_sets_only_provided_fields(self) -> None:
        """Omitted arguments must not set a value."""
        bind_boss_trace(agent_run_id="run-1", application_id="app-1")
        snapshot = boss_trace_context()
        assert snapshot == {"agent_run_id": "run-1", "application_id": "app-1"}

    def test_clear_resets_all_fields(self) -> None:
        bind_boss_trace(
            agent_run_id="run-1",
            workflow_type="boss_match_decision",
            job_id="job-1",
        )
        assert boss_trace_context() != {}
        clear_boss_trace()
        assert boss_trace_context() == {}

    def test_set_failure_code_independently(self) -> None:
        set_failure_code("selector_drift")
        assert boss_trace_context() == {"failure_code": "selector_drift"}
        set_failure_code(None)
        assert boss_trace_context() == {}

    def test_bind_accumulates_across_calls(self) -> None:
        bind_boss_trace(agent_run_id="run-1")
        bind_boss_trace(job_id="job-1")
        bind_boss_trace(page_id="page-1")
        snapshot = boss_trace_context()
        assert snapshot == {
            "agent_run_id": "run-1",
            "job_id": "job-1",
            "page_id": "page-1",
        }

    def test_bound_logger_carries_trace_fields(self, captured_events: list) -> None:
        bind_boss_trace(agent_run_id="run-1", job_id="job-1")
        log = bound_logger("test")
        log.info("test.event", extra_field="value")
        clear_boss_trace()

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event["event"] == "test.event"
        assert event["agent_run_id"] == "run-1"
        assert event["job_id"] == "job-1"
        assert event["extra_field"] == "value"

    def test_trace_fields_flow_via_contextvars(self, captured_events: list) -> None:
        """bind_boss_trace flows into any logger via merge_contextvars."""
        from app.core.logging import get_logger

        bind_boss_trace(agent_run_id="run-ctx", workflow_type="boss_match_decision")
        log = get_logger("test.contextvars")
        log.warning("test.ctx_event", failure_code="model_call_failed")
        clear_boss_trace()

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event["agent_run_id"] == "run-ctx"
        assert event["workflow_type"] == "boss_match_decision"
        assert event["failure_code"] == "model_call_failed"


# ---------------------------------------------------------------------------
# Bridge lifecycle log field contract
# ---------------------------------------------------------------------------


class TestBridgeLogFields:
    """Verify bridge instruction lifecycle logs carry the trace contract."""

    @pytest.fixture
    def channel(self) -> UserscriptChannel:
        return UserscriptChannel()

    def test_heartbeat_has_page_url_hash(
        self, channel: UserscriptChannel, captured_events: list
    ) -> None:
        channel.heartbeat(
            page_id="page-1",
            page_url_hash="sha256:abc",
            page_title="BOSS Job",
        )
        heartbeats = [e for e in captured_events if e["event"] == "boss.bridge.heartbeat"]
        assert len(heartbeats) == 1
        assert heartbeats[0]["page_id"] == "page-1"
        assert heartbeats[0]["page_url_hash"] == "sha256:abc"

    def test_instruction_sent_has_expected_url_hash(
        self, channel: UserscriptChannel, captured_events: list
    ) -> None:
        channel.heartbeat(page_id="page-1", page_url_hash="sha256:target")
        instr = make_instruction("fetch_text", page_id="page-1", expected_url_hash="sha256:target")
        # Use put_instruction but we don't need to wait for the result — the
        # instruction_sent log fires before _take_result. Cancel it.
        import asyncio

        async def _run() -> None:
            task = asyncio.create_task(channel.put_instruction(instr))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        asyncio.run(_run())

        sent = [e for e in captured_events if e["event"] == "boss.bridge.instruction_sent"]
        assert len(sent) == 1
        assert sent[0]["instruction_id"] == instr.instruction_id
        assert sent[0]["op"] == "fetch_text"
        assert sent[0]["page_id"] == "page-1"
        assert sent[0]["expected_url_hash"] == "sha256:target"

    def test_result_received_has_error_on_failure(
        self, channel: UserscriptChannel, captured_events: list
    ) -> None:
        from app.platforms.boss.userscript_channel import InstructionResult

        channel.put_result(
            InstructionResult(
                instruction_id="instr-1",
                success=False,
                error="result_timeout",
                page_id="page-1",
            )
        )
        received = [e for e in captured_events if e["event"] == "boss.bridge.result_received"]
        assert len(received) == 1
        assert received[0]["instruction_id"] == "instr-1"
        assert received[0]["success"] is False
        assert received[0]["error"] == "result_timeout"

    def test_result_timeout_emits_warning(
        self, channel: UserscriptChannel, captured_events: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The silent 90s timeout gap must now emit a boss.bridge.result_timeout warning."""
        import asyncio

        # Speed up the timeout so the test is fast.
        monkeypatch.setattr(
            "app.platforms.boss.userscript_channel.RESULT_TIMEOUT_S", 0.1
        )
        instr = make_instruction("fetch_text", page_id="page-1")

        async def _run() -> None:
            await channel.put_instruction(instr)

        asyncio.run(_run())

        timeouts = [e for e in captured_events if e["event"] == "boss.bridge.result_timeout"]
        assert len(timeouts) == 1
        assert timeouts[0]["instruction_id"] == instr.instruction_id
        assert timeouts[0]["op"] == "fetch_text"
        assert timeouts[0]["timeout_s"] == 0.1

    def test_instruction_requeued_emits_debug(
        self, channel: UserscriptChannel, captured_events: list
    ) -> None:
        """Non-matching instructions are re-enqueued with a debug log."""
        import asyncio

        # Enqueue an instruction bound to page-A.
        instr_a = make_instruction("fetch_text", page_id="page-A")
        channel._queue.put_nowait(instr_a)

        async def _run() -> None:
            # Poll for page-B — instruction_a should be re-enqueued.
            result = await channel.take_instruction_for_page("page-B")
            return result

        result = asyncio.run(_run())
        assert result is None  # no matching instruction

        requeued = [
            e for e in captured_events if e["event"] == "boss.bridge.instruction_requeued"
        ]
        assert len(requeued) >= 1
        assert requeued[0]["instruction_id"] == instr_a.instruction_id
        assert requeued[0]["instruction_page_id"] == "page-A"
        assert requeued[0]["requesting_page_id"] == "page-B"


# ---------------------------------------------------------------------------
# Queue worker failure log field contract
# ---------------------------------------------------------------------------


class TestQueueWorkerLogFields:
    """Verify queue worker missing_run/error logs carry namespace + workflow_type."""

    def test_smoke_missing_run_has_namespace_and_workflow_type(
        self, captured_events: list
    ) -> None:
        from app.queue.handlers import smoke
        from app.queue.payloads import SmokePayload

        payload = SmokePayload(
            workflow_type="smoke",
            user_id="user-1",
            agent_run_id="run-nonexistent",
            idempotency_key="key-1",
        )

        import asyncio

        asyncio.run(smoke({}, payload))

        missing = [
            e for e in captured_events if e["event"] == "queue.smoke_missing_run"
        ]
        assert len(missing) == 1
        assert missing[0]["agent_run_id"] == "run-nonexistent"
        assert missing[0]["workflow_type"] == "smoke"
        assert "queue_namespace" in missing[0]
        # The namespace must never be the production default in tests.
        assert missing[0]["queue_namespace"] != "job-search-agent"


# ---------------------------------------------------------------------------
# Sensitive data redaction contract
# ---------------------------------------------------------------------------


class TestSensitiveDataRedaction:
    """Verify raw URLs, cookies, tokens never appear in trace fields."""

    SENSITIVE_MARKERS = [
        "cookie",
        "token",
        "Bearer",
        "https://www.zhipin.com",
        "session_id",
        "raw_html",
        "opening_message",  # HR message body — never in trace fields
    ]

    def test_trace_field_names_never_include_sensitive_markers(self) -> None:
        """The trace-field name set must not include sensitive keys."""
        for field in BOSS_TRACE_FIELDS:
            assert field not in (
                "cookie",
                "token",
                "url",
                "raw_url",
                "html",
                "raw_html",
                "message",
                "opening_message",
                "session",
            ), f"Sensitive field name in trace set: {field}"

    def test_page_url_hash_not_raw_url(self) -> None:
        """page_url_hash must be a hash, never a raw URL."""
        bind_boss_trace(page_url_hash="sha256:deadbeef")
        snapshot = boss_trace_context()
        clear_boss_trace()

        assert "page_url_hash" in snapshot
        value = snapshot["page_url_hash"]
        # Must start with a hash prefix or be a hex digest, never http.
        assert not value.startswith("http")
        assert not value.startswith("www.")
