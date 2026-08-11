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
#:
#: 90 s accommodates browser background-tab throttling: when a BOSS tab is not
#: the active tab, Chrome/Edge throttle ``setInterval`` to ~60 s. The userscript
#: polls ``next-instruction`` on a 1 s ``setInterval``, but in a background tab
#: that becomes ~60 s. A 90 s result timeout ensures the adapter does not abort
#: before a background-tab poll picks up the instruction and posts back a result.
RESULT_TIMEOUT_S = 90.0

#: If no heartbeat arrives within this window, the userscript is considered
#: disconnected.
#:
#: 120 s accommodates the same background-tab throttling: the userscript sends
#: a heartbeat every 5 s, but in a background tab ``setInterval`` is throttled
#: to ~60 s. A 120 s connection timeout ensures a background tab is not marked
#: disconnected between throttled heartbeats.
CONNECTION_TIMEOUT_S = 120.0


OpKind = Literal[
    "fill",
    "click",
    "check_visible",
    "count",
    "read_title",
    "read_url",
    "read_content",
    "read_jd",
    "click_immediate_communicate",
    "fill_opening_message",
    "send_opening_message",
    "read_communication_result",
    "scan_conversations",
    "probe_elements",
]


@dataclass(frozen=True)
class Instruction:
    """One instruction sent from the backend to the userscript.

    ``fill_value`` is only set for the ``fill`` and ``fill_opening_message``
    ops. ``selector_*`` fields are only set for ops that resolve a DOM locator
    (``fill``, ``click``, ``check_visible``, ``count``,
    ``click_immediate_communicate``, ``fill_opening_message``,
    ``send_opening_message``, ``read_communication_result``). Read ops
    (``read_title``, ``read_url``, ``read_content``, ``read_jd``) need no
    selector.

    ``page_id`` and ``expected_url_hash`` bind the instruction to a specific
    browser tab and page. The userscript must refuse to execute if either does
    not match its current state.

    ``max_text_chars`` limits the total text returned by ``read_jd`` (default
    8000). ``selector_profile`` tells the userscript which extraction profile
    to use (e.g. ``boss_recommended_job_v1``).

    ``extra_selectors`` carries additional CSS selector strings keyed by
    semantic name (e.g. ``"success"``, ``"duplicate"``, ``"error"``,
    ``"message_input"``). It is used by ``read_communication_result`` (3 marker
    groups) and ``send_opening_message`` (Enter-key textarea path) so the
    userscript reads selectors from the backend instead of hardcoding them —
    eliminating selector drift (B1 root cause). Values are raw CSS selector
    strings from :mod:`app.platforms.boss.selectors`.
    """

    instruction_id: str
    op: OpKind
    selector_kind: str | None = None
    selector_value: str | None = None
    selector_name: str | None = None
    fill_value: str | None = None
    page_id: str | None = None
    expected_url_hash: str | None = None
    max_text_chars: int | None = None
    selector_profile: str | None = None
    extra_selectors: dict[str, str] | None = None


@dataclass
class InstructionResult:
    """Result posted back by the userscript for one instruction.

    All fields are sanitized before construction by the API layer. ``url`` is a
    sha256 hash (never the raw URL). ``text`` is a truncated, secret-stripped
    title. ``error`` is a diagnostic-stripped short string.

    ``jd`` is the structured JD dict returned by the ``read_jd`` op. It is
    sanitized by the API layer via :func:`sanitize_jd_result` before being
    stored. It carries only text fields — never raw HTML.

    ``marker_counts`` is returned by the ``read_communication_result`` op. It
    carries raw marker-element counts (``success_count``, ``duplicate_count``,
    ``error_count``) so the backend can classify the result. The userscript
    does **not** decide the classification — it only reports what it sees.
    This keeps the "backend owns classification" design invariant intact.

    ``conversations`` is returned by the ``scan_conversations`` op (Phase 1
    feedback loop). Each entry carries only a hashed conversation key and
    status flags (``replied``/``read``/``unread``) — never chat text, never
    contact names. Sanitized by the API layer before reaching the channel.

    ``page_id`` identifies which browser tab produced the result. The channel
    rejects results whose ``page_id`` does not match the instruction's
    ``page_id``, preventing a wrong tab from consuming another tab's
    instruction.
    """

    instruction_id: str
    success: bool = True
    visible: bool | None = None
    count: int | None = None
    text: str | None = None
    url: str | None = None
    error: str | None = None
    page_id: str | None = None
    jd: dict | None = None
    marker_counts: dict[str, int] | None = None
    conversations: list[dict] | None = None


@dataclass
class PageMeta:
    """Metadata about the currently active browser tab.

    Stored from the heartbeat and used to populate the status endpoint and to
    validate instruction/result page binding. All fields are sanitized — no
    raw URL is ever stored.
    """

    page_id: str
    page_url_hash: str | None = None
    page_title: str | None = None


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
    _active_page: PageMeta | None = None
    _pending_instruction: Instruction | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # --- Heartbeat / connection status ----------------------------------

    def heartbeat(
        self,
        *,
        page_id: str | None = None,
        page_url_hash: str | None = None,
        page_title: str | None = None,
    ) -> None:
        """Record a heartbeat from the userscript.

        When ``page_id`` is provided, the active page metadata is updated. This
        lets the status endpoint report which tab is connected and lets the
        adapter bind instructions to that tab. If ``page_id`` is ``None`` (old
        userscript version), the previous page metadata is retained so the
        channel stays backward-compatible.
        """
        self._last_heartbeat = datetime.now(UTC)
        if page_id is not None:
            self._active_page = PageMeta(
                page_id=page_id,
                page_url_hash=page_url_hash,
                page_title=page_title,
            )
        _log.debug(
            "boss.bridge.heartbeat",
            page_id=page_id,
            page_url_hash=page_url_hash,
        )

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

    @property
    def active_page(self) -> PageMeta | None:
        """Metadata about the currently connected browser tab."""
        return self._active_page

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
        """Clear the instruction queue, result store, and active application.

        Called after each prepare/submit operation completes (success or
        failure) so no stale instructions or results linger. The active page
        metadata is **not** cleared here — it is refreshed by heartbeats and
        only cleared when the connection goes stale.
        """
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._results.clear()
        self._active_application_id = None
        self._pending_instruction = None

    def clear_stale_page(self) -> None:
        """Clear the active page metadata.

        Called when the heartbeat has gone stale (disconnected) so that the
        status endpoint no longer reports a page that may have been closed or
        navigated away. This is the safety mechanism that prevents a stale
        page binding from authorizing instructions to a reconnected-but-different
        tab.
        """
        self._active_page = None

    # --- Instruction / result exchange ----------------------------------

    async def put_instruction(self, instruction: Instruction) -> InstructionResult:
        """Enqueue an instruction and wait for its result.

        This is the adapter's primary entry point: send one instruction, block
        until the userscript posts back a result (or the result timeout fires).

        If the instruction does not carry a ``page_id``, it is auto-populated
        from the active page metadata so the userscript knows which tab should
        execute it. Results whose ``page_id`` does not match the instruction's
        ``page_id`` are rejected — a wrong tab cannot satisfy another tab's
        instruction.
        """
        # Auto-bind page_id from the active page if not explicitly set.
        if instruction.page_id is None and self._active_page is not None:
            instruction = Instruction(
                instruction_id=instruction.instruction_id,
                op=instruction.op,
                selector_kind=instruction.selector_kind,
                selector_value=instruction.selector_value,
                selector_name=instruction.selector_name,
                fill_value=instruction.fill_value,
                page_id=self._active_page.page_id,
                expected_url_hash=instruction.expected_url_hash
                or self._active_page.page_url_hash,
                max_text_chars=instruction.max_text_chars,
                selector_profile=instruction.selector_profile,
                extra_selectors=instruction.extra_selectors,
            )

        await self._queue.put(instruction)
        _log.debug(
            "boss.bridge.instruction_sent",
            instruction_id=instruction.instruction_id,
            op=instruction.op,
            page_id=instruction.page_id,
            expected_url_hash=instruction.expected_url_hash,
        )
        return await self._take_result(instruction)

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

    async def take_instruction_for_page(
        self, page_id: str | None
    ) -> Instruction | None:
        """Long-poll for the next instruction bound to *page_id*.

        When *page_id* is ``None`` (old userscripts that don't send the query
        param), this behaves identically to :meth:`take_instruction` — any
        instruction is returned.

        When *page_id* is provided, only instructions whose ``page_id`` is
        ``None`` (unbound — backward compatible) or equal to *page_id* are
        returned. Non-matching instructions are re-enqueued at the back of
        the queue so the correct tab can still consume them later. If the
        queue contains only non-matching instructions, this method polls until
        the timeout expires and returns ``None``.

        This is the server-side half of page binding: the client-side half
        (userscript refuses mismatched ``page_id``) and the result-side half
        (``put_result`` rejects mismatched ``page_id``) provide defense-in-depth.
        """
        if page_id is None:
            return await self.take_instruction()

        deadline = datetime.now(UTC) + timedelta(seconds=INSTRUCTION_POLL_TIMEOUT_S)
        skipped: list[Instruction] = []
        result: Instruction | None = None
        while datetime.now(UTC) < deadline:
            try:
                remaining = (deadline - datetime.now(UTC)).total_seconds()
                instruction = await asyncio.wait_for(
                    self._queue.get(), timeout=max(remaining, 0.01)
                )
            except TimeoutError:
                break

            if (
                instruction.page_id is None
                or instruction.page_id == page_id
            ):
                result = instruction
                break
            # Non-matching: re-enqueue for the correct tab.
            skipped.append(instruction)
            _log.debug(
                "boss.bridge.instruction_requeued",
                instruction_id=instruction.instruction_id,
                op=instruction.op,
                instruction_page_id=instruction.page_id,
                requesting_page_id=page_id,
            )

        # Put any skipped instructions back so they aren't lost.
        for ins in reversed(skipped):
            await self._queue.put(ins)

        return result

    def put_result(self, result: InstructionResult) -> bool:
        """Post back a result for a completed instruction.

        Returns ``True`` if the result was accepted, ``False`` if it was
        rejected due to a page binding mismatch. A result is rejected when:

        - The instruction that was dispatched carried a ``page_id``.
        - The result's ``page_id`` does not match that instruction's
          ``page_id``.

        This prevents a non-target BOSS tab from consuming another tab's
        instruction and posting back a result.
        """
        instruction = self._pending_instruction
        if (
            instruction is not None
            and instruction.page_id is not None
            and result.page_id is not None
            and result.page_id != instruction.page_id
        ):
            _log.warning(
                "boss.bridge.result_page_mismatch",
                instruction_id=result.instruction_id,
                expected_page_id=instruction.page_id,
                result_page_id=result.page_id,
            )
            return False

        self._results[result.instruction_id] = result
        _log.debug(
            "boss.bridge.result_received",
            instruction_id=result.instruction_id,
            success=result.success,
            page_id=result.page_id,
            error=result.error if not result.success else None,
        )
        return True

    async def _take_result(self, instruction: Instruction) -> InstructionResult:
        """Wait for a result to appear, with a bounded timeout.

        Results whose ``page_id`` does not match the instruction's ``page_id``
        are rejected by :meth:`put_result` and never appear in the store, so
        this method naturally times out if a wrong tab tries to answer.
        """
        self._pending_instruction = instruction
        deadline = datetime.now(UTC) + timedelta(seconds=RESULT_TIMEOUT_S)
        while datetime.now(UTC) < deadline:
            if instruction.instruction_id in self._results:
                return self._results.pop(instruction.instruction_id)
            await asyncio.sleep(0.05)
        # Timeout: the userscript did not respond in time.
        _log.warning(
            "boss.bridge.result_timeout",
            instruction_id=instruction.instruction_id,
            op=instruction.op,
            page_id=instruction.page_id,
            expected_url_hash=instruction.expected_url_hash,
            timeout_s=RESULT_TIMEOUT_S,
        )
        return InstructionResult(
            instruction_id=instruction.instruction_id,
            success=False,
            error=sanitize_diagnostic("result_timeout") or "result_timeout",
            page_id=instruction.page_id,
        )


def make_instruction(
    op: OpKind,
    *,
    selector_kind: str | None = None,
    selector_value: str | None = None,
    selector_name: str | None = None,
    fill_value: str | None = None,
    page_id: str | None = None,
    expected_url_hash: str | None = None,
    max_text_chars: int | None = None,
    selector_profile: str | None = None,
    extra_selectors: dict[str, str] | None = None,
) -> Instruction:
    """Construct an :class:`Instruction` with a generated id."""
    return Instruction(
        instruction_id=f"ins_{uuid.uuid4().hex[:12]}",
        op=op,
        selector_kind=selector_kind,
        selector_value=selector_value,
        selector_name=selector_name,
        fill_value=fill_value,
        page_id=page_id,
        expected_url_hash=expected_url_hash,
        max_text_chars=max_text_chars,
        selector_profile=selector_profile,
        extra_selectors=extra_selectors,
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
    "PageMeta",
    "RESULT_TIMEOUT_S",
    "UserscriptChannel",
    "get_channel",
    "make_instruction",
    "reset_channel",
    "sanitize_result_error",
    "sanitize_result_text",
    "sanitize_result_url",
]
