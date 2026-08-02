"""In-memory instruction queue for the userscript bridge channel.

This module owns the *process-local* communication channel between the backend
adapter (producer) and the Tampermonkey userscript (consumer). It is the
replacement for the Playwright/CDP runtime when BOSS anti-automation detection
makes CDP unusable.

Design invariants (see ``.trellis/spec/backend/authentication.md`` §Bridge
Channel Security):

- **Pure memory.** The queue is an ``asyncio.Queue`` and the result store is a
  plain ``dict``. Neither is ever persisted to Redis or PostgreSQL. If the
  process restarts, both are lost — the human simply re-initiates.
- **Single active application.** Only one ``application_id`` may be active at a
  time. ``set_active_application`` enforces this so two concurrent prepare/submit
  calls cannot interleave instructions for different applications.
- **No raw content.** Instructions carry only operation + selector + fill value.
  Results carry only ``visible``/``count``/``text``/``url``/``error`` — never
  raw HTML, cookies, or tokens.
- **Bounded waits.** ``take_instruction`` and ``take_result`` both time out so a
  disconnected userscript or a stuck backend never hangs the worker.
- **Heartbeat liveness.** The userscript posts a heartbeat every ~5 s. If no
  heartbeat arrives within ``CONNECTION_TIMEOUT_S``, the channel reports
  disconnected and the adapter aborts with ``unknown``.

The module exposes a process-level singleton via :func:`get_channel` so the HTTP
bridge endpoints and the adapter share the same queue instance.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.core.logging import get_logger
from app.platforms.boss.sanitizer import sanitize_diagnostic, sanitize_title, sanitize_url

_log = get_logger("app.platforms.boss.userscript_channel")

#: How long ``take_instruction`` blocks before returning empty (long-poll).
INSTRUCTION_POLL_TIMEOUT_S = 5.0

#: How long the adapter waits for a result before aborting.
RESULT_TIMEOUT_S = 15.0

#: If no heartbeat arrives within this window, the userscript is considered
#: disconnected.
CONNECTION_TIMEOUT_S = 15.0


OpKind = Literal[
    "fill",
    "click",
    "check_visible",
    "count",
    "read_title",
    "read_url",
    "read_content",
]


@dataclass(frozen=True)
class Instruction:
    """One instruction sent from the backend to the userscript.

    ``fill_value`` is only set for the ``fill`` op. ``selector_*`` fields are
    only set for ops that resolve a DOM locator (``fill``, ``click``,
    ``check_visible``, ``count``). Read ops (``read_title``, ``read_url``,
    ``read_content``) need no selector.
    """

    instruction_id: str
    op: OpKind
    selector_kind: str | None = None
    selector_value: str | None = None
    selector_name: str | None = None
    fill_value: str | None = None


@dataclass
class InstructionResult:
    """Result posted back by the userscript for one instruction.

    All fields are sanitized before construction by the API layer. ``url`` is a
    sha256 hash (never the raw URL). ``text`` is a truncated, secret-stripped
    title. ``error`` is a diagnostic-stripped short string.
    """

    instruction_id: str
    success: bool = True
    visible: bool | None = None
    count: int | None = None
    text: str | None = None
    url: str | None = None
    error: str | None = None


@dataclass
class UserscriptChannel:
    """Process-local in-memory bridge between adapter and userscript.

    A single instance is shared by the adapter (producer of instructions) and
    the HTTP bridge endpoints (consumer of instructions / producer of results).
    """

    _queue: asyncio.Queue[Instruction] = field(default_factory=asyncio.Queue)
    _results: dict[str, InstructionResult] = field(default_factory=dict)
    _last_heartbeat: datetime | None = None
    _active_application_id: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # --- Heartbeat / connection status ----------------------------------

    def heartbeat(self) -> None:
        """Record a heartbeat from the userscript."""
        self._last_heartbeat = datetime.now(UTC)
        _log.debug("boss.bridge.heartbeat")

    def is_connected(self) -> bool:
        """Return ``True`` if a heartbeat arrived within the timeout window."""
        if self._last_heartbeat is None:
            return False
        age = datetime.now(UTC) - self._last_heartbeat
        return age <= timedelta(seconds=CONNECTION_TIMEOUT_S)

    @property
    def last_heartbeat(self) -> datetime | None:
        return self._last_heartbeat

    @property
    def active_application_id(self) -> str | None:
        return self._active_application_id

    # --- Active application management ----------------------------------

    async def set_active_application(self, application_id: str) -> None:
        """Set the active application, enforcing single-active invariant.

        If a *different* application is already active and connected, this
        raises ``RuntimeError`` so two concurrent submissions cannot interleave.
        If the previous application is disconnected (stale), it is cleared
        silently.
        """
        async with self._lock:
            if (
                self._active_application_id is not None
                and self._active_application_id != application_id
                and self.is_connected()
            ):
                raise RuntimeError(
                    f"Another application ({self._active_application_id}) is "
                    f"already active on the bridge."
                )
            self._active_application_id = application_id

    def clear(self) -> None:
        """Clear the instruction queue and result store.

        Called after each prepare/submit operation completes (success or
        failure) so no stale instructions or results linger.
        """
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._results.clear()
        self._active_application_id = None

    # --- Instruction / result exchange ----------------------------------

    async def put_instruction(self, instruction: Instruction) -> InstructionResult:
        """Enqueue an instruction and wait for its result.

        This is the adapter's primary entry point: send one instruction, block
        until the userscript posts back a result (or the result timeout fires).
        """
        await self._queue.put(instruction)
        _log.debug(
            "boss.bridge.instruction_sent",
            instruction_id=instruction.instruction_id,
            op=instruction.op,
        )
        return await self._take_result(instruction.instruction_id)

    async def take_instruction(self) -> Instruction | None:
        """Long-poll for the next instruction.

        Returns ``None`` if no instruction arrives within
        :data:`INSTRUCTION_POLL_TIMEOUT_S`. The userscript polls again.
        """
        try:
            return await asyncio.wait_for(
                self._queue.get(), timeout=INSTRUCTION_POLL_TIMEOUT_S
            )
        except TimeoutError:
            return None

    def put_result(self, result: InstructionResult) -> None:
        """Post back a result for a completed instruction."""
        self._results[result.instruction_id] = result
        _log.debug(
            "boss.bridge.result_received",
            instruction_id=result.instruction_id,
            success=result.success,
        )

    async def _take_result(self, instruction_id: str) -> InstructionResult:
        """Wait for a result to appear, with a bounded timeout."""
        deadline = datetime.now(UTC) + timedelta(seconds=RESULT_TIMEOUT_S)
        while datetime.now(UTC) < deadline:
            if instruction_id in self._results:
                return self._results.pop(instruction_id)
            await asyncio.sleep(0.05)
        # Timeout: the userscript did not respond in time.
        return InstructionResult(
            instruction_id=instruction_id,
            success=False,
            error=sanitize_diagnostic("result_timeout") or "result_timeout",
        )


def make_instruction(
    op: OpKind,
    *,
    selector_kind: str | None = None,
    selector_value: str | None = None,
    selector_name: str | None = None,
    fill_value: str | None = None,
) -> Instruction:
    """Construct an :class:`Instruction` with a generated id."""
    return Instruction(
        instruction_id=f"ins_{uuid.uuid4().hex[:12]}",
        op=op,
        selector_kind=selector_kind,
        selector_value=selector_value,
        selector_name=selector_name,
        fill_value=fill_value,
    )


def sanitize_result_text(raw: str | None) -> str | None:
    """Sanitize a text value returned by the userscript (title/content)."""
    return sanitize_title(raw)


def sanitize_result_url(raw_url: str | None) -> str | None:
    """Hash a raw URL returned by the userscript."""
    if raw_url is None:
        return None
    return sanitize_url(raw_url)


def sanitize_result_error(raw: str | None) -> str | None:
    """Sanitize an error string returned by the userscript."""
    return sanitize_diagnostic(raw)


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_channel: UserscriptChannel | None = None


def get_channel() -> UserscriptChannel:
    """Return the process-level :class:`UserscriptChannel` singleton.

    The channel is created on first access and reused for the lifetime of the
    process. Tests that need a fresh channel can call :func:`reset_channel`.
    """
    global _channel  # noqa: PLW0603
    if _channel is None:
        _channel = UserscriptChannel()
    return _channel


def reset_channel() -> None:
    """Reset the singleton. Used by tests to get a clean channel."""
    global _channel  # noqa: PLW0603
    _channel = None


__all__ = [
    "CONNECTION_TIMEOUT_S",
    "INSTRUCTION_POLL_TIMEOUT_S",
    "Instruction",
    "InstructionResult",
    "RESULT_TIMEOUT_S",
    "UserscriptChannel",
    "get_channel",
    "make_instruction",
    "reset_channel",
    "sanitize_result_error",
    "sanitize_result_text",
    "sanitize_result_url",
]
