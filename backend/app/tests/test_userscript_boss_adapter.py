"""Tests for the userscript-backed BOSS adapter.

These tests inject a :class:`FakeUserscriptChannel` that pre-programs
instruction→result mappings. This lets us exercise every prepare/submit
outcome (form_ready, login, captcha, rate limit, duplicate, selector drift,
unknown, submitted) without a real browser or userscript.

Safety invariants verified:

- prepare **never** sends a ``click`` instruction.
- submit sends at most **one** ``click`` instruction.
- disconnected bridge → ``unknown`` outcome.
- the channel is cleared after every operation.
- **P1**: if the userscript is on the wrong page (URL hash mismatch), both
  prepare and submit hard-stop as ``unknown`` before any fill/click.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.platforms.base import (
    CommunicationExecuteContext,
    CommunicationOutcome,
    FilledField,
    FilledPageState,
    FilledSubmissionSnapshot,
    PrepareContext,
    PrepareOutcome,
    SubmitContext,
    SubmitOutcome,
    communication_failure_code,
)
from app.platforms.boss.sanitizer import sanitize_diagnostic, sanitize_url
from app.platforms.boss.selectors import (
    CAPTCHA_MARKER,
    COMMUNICATION_MESSAGE_INPUT,
    COMMUNICATION_SEND_BUTTON,
    CONTINUE_COMMUNICATE_BUTTON,
    DUPLICATE_MARKER,
    FINAL_SUBMIT_BUTTON,
    IMMEDIATE_COMMUNICATE_BUTTON,
    LOGIN_MARKER,
    MESSAGE_INPUT,
    PLATFORM_ERROR_MARKER,
    RATE_LIMIT_MARKER,
    RESUME_UPLOAD,
    SUBMIT_DUPLICATE_MARKER,
    SUCCESS_MARKER,
)
from app.platforms.boss.userscript_adapter import UserscriptBossAdapter, UserscriptBossPage
from app.platforms.boss.userscript_channel import (
    Instruction,
    InstructionResult,
    UserscriptChannel,
)

#: The URL hash of the default test target_resource
#: ``https://www.zhipin.com/job/123``. Used by the page-binding validation
#: (P1): the adapter compares ``sanitize_url(ctx.target_resource)`` with the
#: hash returned by the userscript's ``read_url`` instruction.
_TARGET_URL_HASH = sanitize_url("https://www.zhipin.com/job/123")

#: A deliberately different hash, used to simulate "wrong page".
_WRONG_URL_HASH = "sha256:deadbeef"


# ---------------------------------------------------------------------------
# Fake channel
# ---------------------------------------------------------------------------


class FakeUserscriptChannel(UserscriptChannel):
    """Pre-programmed channel that maps instructions to canned results.

    Tests configure ``result_map`` — a dict from ``(op, selector_value)`` or
    ``(op, selector_value, selector_name)`` to an :class:`InstructionResult`.
    When the adapter sends an instruction, the fake channel looks up the result
    and posts it back immediately.

    The 3-tuple key ``(op, selector_value, selector_name)`` is tried first so
    tests can distinguish selectors that share the same ``selector_value`` —
    notably role selectors like ``IMMEDIATE_COMMUNICATE_BUTTON`` and
    ``CONTINUE_COMMUNICATE_BUTTON`` (both ``value="button"``, differing only
    in ``name``).

    ``connected`` controls ``is_connected()``. ``instructions_sent`` records
    every instruction for post-hoc assertions.
    """

    def __init__(
        self,
        *,
        connected: bool = True,
        result_map: dict[tuple[str, ...], InstructionResult] | None = None,
    ) -> None:
        super().__init__()
        self._connected = connected
        self.result_map = result_map or {}
        self.instructions_sent: list[Instruction] = []
        self._active_application_id: str | None = None  # type: ignore[assignment]

    def is_connected(self) -> bool:  # type: ignore[override]
        return self._connected

    async def put_instruction(  # type: ignore[override]
        self, instruction: Instruction
    ) -> InstructionResult:
        self.instructions_sent.append(instruction)
        # Try the 3-tuple key first (op, selector_value, selector_name) so
        # role selectors with the same value but different names can be
        # distinguished. Fall back to the 2-tuple (op, selector_value).
        key3 = (instruction.op, instruction.selector_value, instruction.selector_name)
        key2 = (instruction.op, instruction.selector_value)
        result = self.result_map.get(key3) or self.result_map.get(key2)
        if result is not None:
            return InstructionResult(
                instruction_id=instruction.instruction_id,
                success=result.success,
                visible=result.visible,
                count=result.count,
                text=result.text,
                url=result.url,
                error=result.error,
                jd=result.jd,
                marker_counts=result.marker_counts,
            )
        # Default: success with no data.
        return InstructionResult(instruction_id=instruction.instruction_id, success=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prepare_ctx(**overrides: Any) -> PrepareContext:
    base: dict[str, Any] = {
        "application_id": "app-1",
        "target_platform": "boss",
        "target_resource": "https://www.zhipin.com/job/123",
        "selected_artifact_ids": ["art-1"],
        "outgoing_text": "您好，我对这个岗位很感兴趣。",
        "resume_file_reference": "resume-v1",
        "source_hash": "sha256:abc",
        "session_reference": None,
    }
    base.update(overrides)
    return PrepareContext(**base)


def _filled_snapshot(**overrides: Any) -> FilledSubmissionSnapshot:
    base: dict[str, Any] = {
        "target_platform": "boss",
        "target_resource": "https://www.zhipin.com/job/123",
        "application_id": "app-1",
        "selected_artifact_ids": ["art-1"],
        "resume_file_reference": "resume-v1",
        "fields": [
            FilledField(
                name="message",
                label="开场白",
                value="您好，我对这个岗位很感兴趣。",
            )
        ],
        "attachments": [],
        "page_state": FilledPageState(
            url_hash="sha256:abcd1234",
            title="BOSS",
            final_submit_selector_seen=True,
        ),
        "captured_at": datetime.now(UTC),
    }
    base.update(overrides)
    return FilledSubmissionSnapshot(**base)


def _submit_ctx(**overrides: Any) -> SubmitContext:
    base: dict[str, Any] = {
        "application_id": "app-1",
        "target_platform": "boss",
        "target_resource": "https://www.zhipin.com/job/123",
        "filled_snapshot": _filled_snapshot(),
        "session_reference": None,
    }
    base.update(overrides)
    return SubmitContext(**base)


def _visible_result(visible: bool = True) -> InstructionResult:
    return InstructionResult(instruction_id="_", success=True, visible=visible)


def _count_result(count: int) -> InstructionResult:
    return InstructionResult(instruction_id="_", success=True, count=count)


def _ok_result() -> InstructionResult:
    return InstructionResult(instruction_id="_", success=True)


def _fail_result(error: str = "op_failed") -> InstructionResult:
    return InstructionResult(instruction_id="_", success=False, error=error)


def _text_result(text: str) -> InstructionResult:
    return InstructionResult(instruction_id="_", success=True, text=text)


def _marker_result(
    *,
    success_count: int = 0,
    duplicate_count: int = 0,
    error_count: int = 0,
) -> InstructionResult:
    """Build a read_communication_result with raw marker counts."""
    return InstructionResult(
        instruction_id="_",
        success=True,
        marker_counts={
            "success_count": success_count,
            "duplicate_count": duplicate_count,
            "error_count": error_count,
        },
    )


def _url_result(url_hash: str = _TARGET_URL_HASH) -> InstructionResult:
    return InstructionResult(instruction_id="_", success=True, url=url_hash)


def _jd_result(
    jd: dict | None = None,
    *,
    success: bool = True,
    error: str | None = None,
) -> InstructionResult:
    """Build a read_jd result with a canned JD dict."""
    if jd is None:
        jd = {
            "title": "高级前端工程师",
            "company": "某科技公司",
            "location": "北京",
            "salary": "25-40K·14薪",
            "experience": "3-5年",
            "education": "本科",
            "skills": ["React", "TypeScript", "Node.js"],
            "description": "负责前端架构设计和核心功能开发。",
            "source_kind": "boss_recommended_job",
            "page_url_hash": _TARGET_URL_HASH,
        }
    return InstructionResult(
        instruction_id="_",
        success=success,
        jd=jd,
        error=error,
    )


# ---------------------------------------------------------------------------
# read_jd: JD read via the userscript bridge
# ---------------------------------------------------------------------------


async def test_read_current_jd_returns_sanitized_dict() -> None:
    """read_current_jd sends a read_jd instruction and returns the JD dict."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_jd", None): _jd_result(),
        }
    )
    page = UserscriptBossPage(ch)
    jd = await page.read_current_jd()
    assert jd is not None
    assert jd["title"] == "高级前端工程师"
    assert jd["company"] == "某科技公司"
    assert jd["skills"] == ["React", "TypeScript", "Node.js"]
    assert jd["source_kind"] == "boss_recommended_job"
    # The instruction carried the selector_profile and max_text_chars.
    ins = ch.instructions_sent[0]
    assert ins.op == "read_jd"
    assert ins.selector_profile == "boss_recommended_job_v1"
    assert ins.max_text_chars == 8000


async def test_read_current_jd_returns_none_on_failure() -> None:
    """If the read_jd instruction fails, read_current_jd returns None."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_jd", None): _jd_result(success=False, error="extraction_failed"),
        }
    )
    page = UserscriptBossPage(ch)
    jd = await page.read_current_jd()
    assert jd is None


async def test_read_current_jd_returns_none_when_jd_is_none() -> None:
    """If the result has success=True but jd=None, return None."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_jd", None): InstructionResult(instruction_id="_", success=True),
        }
    )
    page = UserscriptBossPage(ch)
    jd = await page.read_current_jd()
    assert jd is None


async def test_read_current_jd_custom_params_forwarded() -> None:
    """Custom max_text_chars and selector_profile are forwarded to the instruction."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_jd", None): _jd_result(),
        }
    )
    page = UserscriptBossPage(ch)
    await page.read_current_jd(max_text_chars=4000, selector_profile="custom_v2")
    ins = ch.instructions_sent[0]
    assert ins.max_text_chars == 4000
    assert ins.selector_profile == "custom_v2"


# ---------------------------------------------------------------------------
# Prepare: disconnected
# ---------------------------------------------------------------------------


async def test_prepare_disconnected_returns_unknown() -> None:
    ch = FakeUserscriptChannel(connected=False)
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.unknown
    assert result.failure_code == "platform_unknown_result"
    # No instructions should be sent when disconnected.
    assert ch.instructions_sent == []


# ---------------------------------------------------------------------------
# Prepare: page-binding (P1)
# ---------------------------------------------------------------------------


async def test_prepare_wrong_page_returns_unknown() -> None:
    """If the userscript is on a different page than target_resource → unknown.

    P1 safety: the adapter must never fill on the wrong BOSS tab. The URL hash
    mismatch is detected before any classify/fill/click.
    """
    ch = FakeUserscriptChannel(
        result_map={
            # read_url returns a hash that does NOT match the target.
            ("read_url", None): _url_result(_WRONG_URL_HASH),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.unknown
    assert result.diagnostic_reference == sanitize_diagnostic("page_mismatch")
    # No fill/click instructions should have been sent on the wrong page.
    fill_or_click = [ins for ins in ch.instructions_sent if ins.op in ("fill", "click")]
    assert fill_or_click == []


# ---------------------------------------------------------------------------
# Prepare: classifier hard stops
# ---------------------------------------------------------------------------


async def test_prepare_login_required() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(True),
            ("count", LOGIN_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.login_required


async def test_prepare_captcha_required() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(True),
            ("count", CAPTCHA_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.captcha_required


async def test_prepare_rate_limited() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.rate_limited


async def test_prepare_duplicate_detected() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.duplicate_detected


async def test_prepare_selector_drift_missing_anchors() -> None:
    """If the form anchors (message input + submit button) are missing → drift."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            # Both form anchors return 0 count / not visible.
            ("count", MESSAGE_INPUT.value): _count_result(0),
            ("check_visible", MESSAGE_INPUT.value): _visible_result(False),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.selector_drift


# ---------------------------------------------------------------------------
# Prepare: form_ready → filled_preview
# ---------------------------------------------------------------------------


async def test_prepare_filled_preview_success() -> None:
    """Form ready, message filled, submit visible → filled_preview."""
    ch = FakeUserscriptChannel(
        result_map={
            # Page binding matches.
            ("read_url", None): _url_result(),
            # Classifier: no failure markers.
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            # Form anchors present.
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            # Fill message succeeds.
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            # Resume upload visible.
            ("check_visible", RESUME_UPLOAD.value): _visible_result(True),
            # Final submit visible (never clicked).
            ("check_visible", FINAL_SUBMIT_BUTTON.value): _visible_result(True),
            # Diagnostics.
            ("read_title", None): _text_result("BOSS Chat"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.filled_preview
    assert result.snapshot is not None
    assert result.snapshot.fields[0].name == "message"
    assert result.snapshot.page_state.final_submit_selector_seen is True
    assert result.snapshot.page_state.url_hash == _TARGET_URL_HASH
    assert result.snapshot.page_state.title == "BOSS Chat"

    # Safety: no click instruction was sent during prepare.
    click_ops = [ins for ins in ch.instructions_sent if ins.op == "click"]
    assert click_ops == []


async def test_prepare_fill_failure_returns_selector_drift() -> None:
    """If the fill instruction fails → selector_drift."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _fail_result("fill_error"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.selector_drift


async def test_prepare_submit_not_visible_returns_drift() -> None:
    """Form ready + message filled but submit not visible → selector_drift."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("check_visible", RESUME_UPLOAD.value): _visible_result(False),
            ("check_visible", FINAL_SUBMIT_BUTTON.value): _visible_result(False),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome == PrepareOutcome.selector_drift


async def test_prepare_no_outgoing_text_skips_fill() -> None:
    """When outgoing_text is None, the message fill is skipped."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("check_visible", FINAL_SUBMIT_BUTTON.value): _visible_result(True),
            ("read_title", None): _text_result("BOSS"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.prepare_submission(_prepare_ctx(outgoing_text=None))
    assert result.outcome == PrepareOutcome.filled_preview
    assert result.snapshot is not None
    assert result.snapshot.fields == []


# ---------------------------------------------------------------------------
# Submit: disconnected
# ---------------------------------------------------------------------------


async def test_submit_disconnected_returns_unknown() -> None:
    ch = FakeUserscriptChannel(connected=False)
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.unknown
    assert ch.instructions_sent == []


# ---------------------------------------------------------------------------
# Submit: page-binding (P1)
# ---------------------------------------------------------------------------


async def test_submit_wrong_page_returns_unknown() -> None:
    """If the userscript is on a different page than target_resource → unknown.

    P1 safety: the adapter must never click "send" on the wrong BOSS tab.
    The URL hash mismatch is detected before any classify/fill/click.
    """
    ch = FakeUserscriptChannel(
        result_map={
            # read_url returns a hash that does NOT match the target.
            ("read_url", None): _url_result(_WRONG_URL_HASH),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.unknown
    assert result.diagnostic_reference == sanitize_diagnostic("page_mismatch")
    # No click instruction should have been sent.
    assert [ins for ins in ch.instructions_sent if ins.op == "click"] == []


# ---------------------------------------------------------------------------
# Submit: classifier hard stops
# ---------------------------------------------------------------------------


async def test_submit_login_required_hard_stop() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(True),
            ("count", LOGIN_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.unknown


async def test_submit_duplicate_before_click() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.duplicate_detected
    # No click should have been sent.
    assert [ins for ins in ch.instructions_sent if ins.op == "click"] == []


# ---------------------------------------------------------------------------
# Submit: success
# ---------------------------------------------------------------------------


async def test_submit_success() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            # Classifier: form ready.
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            # Re-apply message.
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            # Click succeeds.
            ("click", FINAL_SUBMIT_BUTTON.value): _ok_result(),
            # Post-submit: success marker.
            ("count", SUCCESS_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.submitted

    # Safety: exactly one click.
    clicks = [ins for ins in ch.instructions_sent if ins.op == "click"]
    assert len(clicks) == 1


async def test_submit_duplicate_after_click() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("click", FINAL_SUBMIT_BUTTON.value): _ok_result(),
            ("count", SUCCESS_MARKER.value): _count_result(0),
            ("count", SUBMIT_DUPLICATE_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.duplicate_detected


async def test_submit_platform_failure_after_click() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("click", FINAL_SUBMIT_BUTTON.value): _ok_result(),
            ("count", SUCCESS_MARKER.value): _count_result(0),
            ("count", SUBMIT_DUPLICATE_MARKER.value): _count_result(0),
            ("count", PLATFORM_ERROR_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.platform_failure


async def test_submit_ambiguous_after_click_returns_unknown() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("click", FINAL_SUBMIT_BUTTON.value): _ok_result(),
            # No success, no duplicate, no error → ambiguous.
            ("count", SUCCESS_MARKER.value): _count_result(0),
            ("count", SUBMIT_DUPLICATE_MARKER.value): _count_result(0),
            ("count", PLATFORM_ERROR_MARKER.value): _count_result(0),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.unknown


async def test_submit_click_failure_returns_platform_failure() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("click", FINAL_SUBMIT_BUTTON.value): _fail_result("click_failed"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.platform_failure


async def test_submit_refill_failure_returns_platform_failure() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _fail_result("refill_failed"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.submit_prepared(_submit_ctx())
    assert result.outcome == SubmitOutcome.platform_failure


# ---------------------------------------------------------------------------
# Safety: clear() is called after every operation
# ---------------------------------------------------------------------------


async def test_prepare_clears_channel_after_success() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("check_visible", RESUME_UPLOAD.value): _visible_result(False),
            ("check_visible", FINAL_SUBMIT_BUTTON.value): _visible_result(True),
            ("read_title", None): _text_result("BOSS"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.prepare_submission(_prepare_ctx())
    assert ch.active_application_id is None


async def test_prepare_clears_channel_after_failure() -> None:
    ch = FakeUserscriptChannel(connected=False)
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.prepare_submission(_prepare_ctx())
    assert ch.active_application_id is None


async def test_submit_clears_channel_after_success() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", LOGIN_MARKER.value): _visible_result(False),
            ("count", LOGIN_MARKER.value): _count_result(0),
            ("check_visible", CAPTCHA_MARKER.value): _visible_result(False),
            ("count", CAPTCHA_MARKER.value): _count_result(0),
            ("count", RATE_LIMIT_MARKER.value): _count_result(0),
            ("count", DUPLICATE_MARKER.value): _count_result(0),
            ("count", MESSAGE_INPUT.value): _count_result(1),
            ("count", FINAL_SUBMIT_BUTTON.value): _count_result(1),
            ("fill", MESSAGE_INPUT.value): _ok_result(),
            ("click", FINAL_SUBMIT_BUTTON.value): _ok_result(),
            ("count", SUCCESS_MARKER.value): _count_result(1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.submit_prepared(_submit_ctx())
    assert ch.active_application_id is None


# ---------------------------------------------------------------------------
# execute_communication: helpers
# ---------------------------------------------------------------------------


def _communicate_ctx(**overrides: Any) -> CommunicationExecuteContext:
    base: dict[str, Any] = {
        "application_id": "app-1",
        "target_platform": "boss",
        "target_resource": "https://www.zhipin.com/job/123",
        "opening_message": "您好，我对这个岗位很感兴趣。",
        "source_hash": "sha256:abc",
        "session_reference": None,
    }
    base.update(overrides)
    return CommunicationExecuteContext(**base)


# ---------------------------------------------------------------------------
# execute_communication: disconnected
# ---------------------------------------------------------------------------


async def test_communicate_disconnected_returns_unknown() -> None:
    ch = FakeUserscriptChannel(connected=False)
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.unknown
    assert result.diagnostic_reference == sanitize_diagnostic("bridge_not_connected")
    assert ch.instructions_sent == []


# ---------------------------------------------------------------------------
# execute_communication: page-binding (P1)
# ---------------------------------------------------------------------------


async def test_communicate_wrong_page_returns_unknown() -> None:
    """If the userscript is on a different page → unknown, no clicks sent."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(_WRONG_URL_HASH),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.unknown
    assert result.diagnostic_reference == sanitize_diagnostic("page_binding_mismatch")
    # No communicate clicks should have been sent.
    assert [
        ins
        for ins in ch.instructions_sent
        if ins.op in ("click_immediate_communicate", "send_opening_message")
    ] == []


# ---------------------------------------------------------------------------
# execute_communication: immediate button missing → failed
# ---------------------------------------------------------------------------


async def test_communicate_immediate_button_missing_returns_failed() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            # Drift check passes: immediate button is visible.
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            # The click_immediate_communicate instruction fails.
            (
                "click_immediate_communicate",
                IMMEDIATE_COMMUNICATE_BUTTON.value,
            ): _fail_result("element_not_found"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.failed
    assert result.failure_code == "immediate_button_missing"


# ---------------------------------------------------------------------------
# execute_communication: message input missing → failed
# ---------------------------------------------------------------------------


async def test_communicate_message_input_missing_returns_failed() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            (
                "click_immediate_communicate",
                IMMEDIATE_COMMUNICATE_BUTTON.value,
            ): _ok_result(),
            # The fill_opening_message instruction fails.
            (
                "fill_opening_message",
                COMMUNICATION_MESSAGE_INPUT.value,
            ): _fail_result("element_not_found"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.failed
    assert result.failure_code == "message_input_missing"


# ---------------------------------------------------------------------------
# execute_communication: page hash changed after fill → unknown
# ---------------------------------------------------------------------------


async def test_communicate_page_hash_changed_after_fill_returns_unknown() -> None:
    """If the page URL hash changes between the initial check and the
    post-fill check → unknown (stale action)."""
    call_count = [0]

    class _ChangingHashChannel(FakeUserscriptChannel):
        async def put_instruction(  # type: ignore[override]
            self, instruction: Instruction
        ) -> InstructionResult:
            self.instructions_sent.append(instruction)
            if instruction.op == "read_url":
                call_count[0] += 1
                if call_count[0] == 1:
                    return _url_result(_TARGET_URL_HASH)
                # Second read_url (post-fill check) returns a different hash.
                return _url_result(_WRONG_URL_HASH)
            key3 = (instruction.op, instruction.selector_value, instruction.selector_name)
            key2 = (instruction.op, instruction.selector_value)
            result = self.result_map.get(key3) or self.result_map.get(key2)
            if result is not None:
                return InstructionResult(
                    instruction_id=instruction.instruction_id,
                    success=result.success,
                    visible=result.visible,
                    count=result.count,
                    text=result.text,
                    url=result.url,
                    error=result.error,
                    jd=result.jd,
                )
            return InstructionResult(instruction_id=instruction.instruction_id, success=True)

    ch = _ChangingHashChannel(
        result_map={
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.unknown
    assert result.diagnostic_reference == sanitize_diagnostic("page_binding_mismatch")
    # send_opening_message should NOT have been sent.
    assert [ins for ins in ch.instructions_sent if ins.op == "send_opening_message"] == []


# ---------------------------------------------------------------------------
# execute_communication: send fails → failed
# ---------------------------------------------------------------------------


async def test_communicate_send_failure_returns_failed() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            (
                "send_opening_message",
                COMMUNICATION_SEND_BUTTON.value,
            ): _fail_result("element_not_found"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.failed
    assert result.failure_code == "send_result_unknown"


# ---------------------------------------------------------------------------
# execute_communication: success
# ---------------------------------------------------------------------------


async def test_communicate_success() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            # read_communication_result returns marker counts. Backend
            # classifies: success_count > 0 → succeeded.
            ("read_communication_result", None): _marker_result(success_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.succeeded

    # Safety: at most 1 click_immediate_communicate + 1 send_opening_message.
    immediate_clicks = [
        ins for ins in ch.instructions_sent if ins.op == "click_immediate_communicate"
    ]
    send_clicks = [ins for ins in ch.instructions_sent if ins.op == "send_opening_message"]
    assert len(immediate_clicks) == 1
    assert len(send_clicks) == 1


# ---------------------------------------------------------------------------
# execute_communication: duplicate
# ---------------------------------------------------------------------------


async def test_communicate_duplicate() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            # duplicate_count > 0 → duplicate (checked before success).
            ("read_communication_result", None): _marker_result(duplicate_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.duplicate


# ---------------------------------------------------------------------------
# execute_communication: success takes priority over duplicate
# ---------------------------------------------------------------------------


async def test_communicate_success_priority_over_duplicate() -> None:
    """When both success and duplicate markers are present, success wins.

    After a successful send, the job-detail button changes from "立即沟通" to
    "继续沟通" (the duplicate marker), AND the chat panel shows the sent
    message (the success marker). Success must be checked first so a
    successful send is not misclassified as a duplicate.
    """
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(success_count=1, duplicate_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.succeeded


# ---------------------------------------------------------------------------
# execute_communication: platform_failure via error markers
# ---------------------------------------------------------------------------


async def test_communicate_platform_failure() -> None:
    """error_count > 0 (and no duplicate/success) → platform_failure.

    This covers the B1 fix: the error selector now matches
    PLATFORM_ERROR_MARKER, and the backend classifies it as failed.
    """
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(error_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.failed
    assert result.failure_code == communication_failure_code(CommunicationOutcome.failed)


# ---------------------------------------------------------------------------
# execute_communication: read_communication_result instruction fails → unknown
# ---------------------------------------------------------------------------


async def test_communicate_read_result_failure_returns_unknown() -> None:
    """If the read_communication_result instruction itself fails, hard stop."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _fail_result("read_failed"),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.unknown
    assert result.failure_code == "send_result_unknown"
    assert result.diagnostic_reference == sanitize_diagnostic("read_result_failed")


# ---------------------------------------------------------------------------
# execute_communication: unknown (ambiguous post-send state)
# ---------------------------------------------------------------------------


async def test_communicate_unknown_result() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            # All counts zero → unknown (hard stop).
            ("read_communication_result", None): _marker_result(),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())
    assert result.outcome == CommunicationOutcome.unknown
    assert result.failure_code == "send_result_unknown"


# ---------------------------------------------------------------------------
# execute_communication: clears channel after every operation
# ---------------------------------------------------------------------------


async def test_communicate_clears_channel_after_success() -> None:
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(success_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.execute_communication(_communicate_ctx())
    assert ch.active_application_id is None


async def test_communicate_clears_channel_after_failure() -> None:
    ch = FakeUserscriptChannel(connected=False)
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.execute_communication(_communicate_ctx())
    assert ch.active_application_id is None


# ---------------------------------------------------------------------------
# extra_selectors: read_communication_result and send_opening_message carry
# selector groups from selectors.py so the userscript does not hardcode them.
# ---------------------------------------------------------------------------


async def test_communicate_read_result_carries_extra_selectors() -> None:
    """read_communication_result must carry success/duplicate/error selectors.

    The selector values must match the constants in selectors.py so the
    userscript uses the backend-defined selectors instead of its own
    hardcoded copy. This is the B1 drift fix.
    """
    from app.platforms.boss.selectors import (
        COMMUNICATION_DUPLICATE_MARKER,
        COMMUNICATION_SUCCESS_MARKER,
        PLATFORM_ERROR_MARKER,
    )

    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(success_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.execute_communication(_communicate_ctx())

    read_ins = [ins for ins in ch.instructions_sent if ins.op == "read_communication_result"]
    assert len(read_ins) == 1
    extra = read_ins[0].extra_selectors
    assert extra is not None
    assert extra["success"] == COMMUNICATION_SUCCESS_MARKER.value
    assert extra["duplicate"] == COMMUNICATION_DUPLICATE_MARKER.value
    assert extra["error"] == PLATFORM_ERROR_MARKER.value


async def test_communicate_send_carries_message_input_selector() -> None:
    """send_opening_message must carry the message_input selector.

    The Enter-key path in the userscript reads this selector instead of
    hardcoding the textarea CSS, keeping it in sync with
    COMMUNICATION_MESSAGE_INPUT in selectors.py.
    """
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(success_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    await adapter.execute_communication(_communicate_ctx())

    send_ins = [ins for ins in ch.instructions_sent if ins.op == "send_opening_message"]
    assert len(send_ins) == 1
    extra = send_ins[0].extra_selectors
    assert extra is not None
    assert extra["message_input"] == COMMUNICATION_MESSAGE_INPUT.value


async def test_communicate_selector_drift_returns_failed() -> None:
    """Both entry buttons invisible → selector_drift failure.

    Step 3.5 probes IMMEDIATE_COMMUNICATE_BUTTON and
    CONTINUE_COMMUNICATE_BUTTON visibility before clicking. If both are
    invisible, the page markup has drifted and we must not guess.
    """
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            # Both entry buttons invisible → drift.
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                False
            ),
            ("check_visible", CONTINUE_COMMUNICATE_BUTTON.value, "继续沟通"): _visible_result(
                False
            ),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())

    assert result.outcome == CommunicationOutcome.failed
    assert result.failure_code == "selector_drift"
    assert result.diagnostic_reference == sanitize_diagnostic(
        "selector_drift_immediate_communicate"
    )

    # Safety: no click instructions sent when drift is detected.
    clicks = [
        ins
        for ins in ch.instructions_sent
        if ins.op in ("click_immediate_communicate", "send_opening_message")
    ]
    assert clicks == []


async def test_communicate_drift_check_passes_when_immediate_visible() -> None:
    """Immediate button visible → normal flow proceeds, no drift failure.

    The drift check (Step 3.5) sends a check_visible for
    IMMEDIATE_COMMUNICATE_BUTTON. When it returns visible=True, the flow
    continues to click + fill + send + classify as normal.
    """
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            # Immediate button visible → drift check passes.
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value, "立即沟通"): _visible_result(
                True
            ),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(success_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())

    assert result.outcome == CommunicationOutcome.succeeded

    # Drift check sent exactly 1 check_visible for the immediate button
    # (by selector_name, since both buttons share selector_value="button").
    drift_checks = [
        ins
        for ins in ch.instructions_sent
        if ins.op == "check_visible" and ins.selector_name == "立即沟通"
    ]
    assert len(drift_checks) == 1
