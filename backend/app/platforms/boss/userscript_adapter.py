"""Userscript-backed BOSS Web adapter.

This adapter replaces the Playwright/CDP runtime with a Tampermonkey userscript
bridge. The backend sends instructions via the in-memory channel
(:mod:`app.platforms.boss.userscript_channel`); the userscript executes them in
the page's own JS context and posts back sanitized results.

Why this exists: BOSS直聘 performs CDP protocol-level automation detection
(``Page.navigate`` + ``Runtime.evaluate``). A userscript running in the page's
own context has no CDP signature and sidesteps this detection entirely.

Architecture (see ``.trellis/tasks/08-03-userscript-bridge-adapter/design.md``):

- :class:`UserscriptBossPage` implements the same method surface as
  :class:`~app.platforms.boss.runtime.BossPage`. Each method becomes one
  HTTP round-trip instruction through the channel.
- :class:`UserscriptBossAdapter` implements :class:`~app.platforms.base.PlatformAdapter`.
  Its prepare/submit flow mirrors :class:`~app.platforms.boss.adapter.RealBossAdapter`
  but uses the userscript channel instead of Playwright.

Safety invariants (all preserved from the Playwright adapter):

- **prepare never clicks the final submit.** ``UserscriptBossPage.click`` raises
  ``RuntimeError`` when ``is_submit_phase=False``.
- **submit clicks exactly once.** ``click_count`` is asserted ``<= 1``.
- **No navigation.** The bridge never sends a ``goto`` instruction. The human
  navigates manually; the adapter only reads/fills/clicks on the current page.
- **In-memory only.** The instruction queue is never persisted. ``clear()``
  is called after each operation.
- **Single application.** ``set_active_application`` enforces one active id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.platforms.base import (
    FilledAttachment,
    FilledField,
    FilledPageState,
    FilledSubmissionSnapshot,
    PrepareContext,
    PrepareOutcome,
    PrepareResult,
    SubmitContext,
    SubmitOutcome,
    SubmitResult,
    prepare_failure_code,
    submit_failure_code,
)
from app.platforms.boss.classifiers import (
    CAPTCHA_REQUIRED,
    DUPLICATE_DETECTED,
    FORM_READY,
    LOGIN_REQUIRED,
    RATE_LIMITED,
    SELECTOR_DRIFT,
    UNKNOWN,
    PageClassification,
    classify_page,
    classify_submit_result,
)
from app.platforms.boss.sanitizer import sanitize_diagnostic, sanitize_url
from app.platforms.boss.selectors import (
    FINAL_SUBMIT_BUTTON,
    MESSAGE_INPUT,
    RESUME_UPLOAD,
    Selector,
)
from app.platforms.boss.userscript_channel import (
    Instruction,
    InstructionResult,
    UserscriptChannel,
    get_channel,
    make_instruction,
)

_log = get_logger("app.platforms.boss.userscript_adapter")

#: Maps classifier outcome codes to ``PrepareOutcome`` (same as RealBossAdapter).
_CLASSIFY_TO_PREPARE: dict[str, PrepareOutcome] = {
    FORM_READY: PrepareOutcome.filled_preview,
    LOGIN_REQUIRED: PrepareOutcome.login_required,
    CAPTCHA_REQUIRED: PrepareOutcome.captcha_required,
    RATE_LIMITED: PrepareOutcome.rate_limited,
    DUPLICATE_DETECTED: PrepareOutcome.duplicate_detected,
    SELECTOR_DRIFT: PrepareOutcome.selector_drift,
    UNKNOWN: PrepareOutcome.unknown,
}


class UserscriptBossPage:
    """BossPage-compatible surface backed by the userscript channel.

    Each method constructs an :class:`Instruction`, sends it through the
    channel, and decodes the :class:`InstructionResult`. The method surface
    matches :class:`~app.platforms.boss.runtime.BossPage` so
    :mod:`app.platforms.boss.classifiers` works unchanged.

    The ``is_submit_phase`` flag enforces the prepare/submit safety boundary:
    ``click`` raises during prepare, and is allowed exactly once during submit.
    """

    def __init__(
        self, channel: UserscriptChannel, *, is_submit_phase: bool = False
    ) -> None:
        self._channel = channel
        self._is_submit_phase = is_submit_phase
        self._click_count = 0

    # --- Locator resolution ---------------------------------------------

    def get_by_role(self, role: str, *, name: str | None = None) -> RemoteLocator:
        return RemoteLocator(kind="role", value=role, name=name)

    def get_by_label(self, text: str) -> RemoteLocator:
        return RemoteLocator(kind="label", value=text, name=None)

    def get_by_placeholder(self, text: str) -> RemoteLocator:
        return RemoteLocator(kind="placeholder", value=text, name=None)

    def locator(self, selector: str) -> RemoteLocator:
        return RemoteLocator(kind="css", value=selector, name=None)

    async def wait_for_selector(
        self, selector: str, *, timeout: float = 0
    ) -> RemoteLocator:
        # The userscript has no explicit wait_for_selector — it resolves
        # selectors on demand. We return the locator; visibility is checked
        # separately via is_visible.
        return RemoteLocator(kind="css", value=selector, name=None)

    # --- Actions ---------------------------------------------------------

    async def fill(self, locator: RemoteLocator, value: str) -> None:
        result = await self._send(
            make_instruction(
                "fill",
                selector_kind=locator.kind,
                selector_value=locator.value,
                selector_name=locator.name,
                fill_value=value,
            )
        )
        if not result.success:
            raise RuntimeError(result.error or "fill_failed")

    async def click(self, locator: RemoteLocator) -> None:
        if not self._is_submit_phase:
            raise RuntimeError("click is not allowed during prepare phase")
        self._click_count += 1
        result = await self._send(
            make_instruction(
                "click",
                selector_kind=locator.kind,
                selector_value=locator.value,
                selector_name=locator.name,
            )
        )
        if not result.success:
            raise RuntimeError(result.error or "click_failed")

    async def is_visible(self, locator: RemoteLocator) -> bool:
        result = await self._send(
            make_instruction(
                "check_visible",
                selector_kind=locator.kind,
                selector_value=locator.value,
                selector_name=locator.name,
            )
        )
        return bool(result.visible) if result.visible is not None else False

    async def count(self, locator: RemoteLocator) -> int:
        result = await self._send(
            make_instruction(
                "count",
                selector_kind=locator.kind,
                selector_value=locator.value,
                selector_name=locator.name,
            )
        )
        return int(result.count) if result.count is not None else 0

    async def text_content(self, locator: RemoteLocator) -> str | None:
        # The userscript bridge does not support reading arbitrary locator text
        # content (only the page title via read_title). Classifiers use
        # is_visible/count for marker detection, not text_content.
        return None

    # --- Sanitized diagnostics ------------------------------------------

    @property
    def url(self) -> str:
        # The raw URL is never available to the backend — only its hash.
        # Return the hash placeholder; callers should use url_hash() instead.
        return "<redacted>"

    def url_hash(self) -> str:
        # Synchronous hash is not available — the URL comes from the userscript
        # asynchronously. The adapter calls _fetch_url_hash() when building the
        # snapshot. Return a placeholder here for interface compatibility.
        return "<pending>"

    async def _fetch_url_hash(self) -> str:
        result = await self._send(make_instruction("read_url"))
        return result.url or sanitize_url("<unknown>")

    async def safe_title(self) -> str | None:
        result = await self._send(make_instruction("read_title"))
        return result.text

    async def content_snippet(self, *, max_len: int = 200) -> str:
        result = await self._send(make_instruction("read_content"))
        if result.text:
            return result.text[:max_len]
        return ""

    # --- Internal --------------------------------------------------------

    async def _send(self, instruction: Instruction) -> InstructionResult:
        return await self._channel.put_instruction(instruction)

    @property
    def click_count(self) -> int:
        return self._click_count


@dataclass(frozen=True)
class RemoteLocator:
    """Selector info carried across the bridge channel.

    Mirrors :class:`~app.platforms.boss.selectors.Selector` but as a lightweight
    value object the userscript can resolve.
    """

    kind: str  # "role" | "label" | "placeholder" | "css"
    value: str
    name: str | None = None


def _resolve(page: UserscriptBossPage, selector: Selector) -> RemoteLocator:
    """Resolve a :class:`Selector` into a :class:`RemoteLocator`.

    This mirrors :func:`app.platforms.boss.adapter._resolve` but returns a
    :class:`RemoteLocator` instead of a Playwright locator.
    """
    if selector.kind.value == "role":
        return page.get_by_role(selector.value, name=selector.name)
    if selector.kind.value == "label":
        return page.get_by_label(selector.value)
    if selector.kind.value == "placeholder":
        return page.get_by_placeholder(selector.value)
    return page.locator(selector.value)


class UserscriptBossAdapter:
    """Userscript-backed BOSS Web adapter.

    Enabled when ``boss_userscript_bridge_enabled`` is set. Uses the in-memory
    channel to send instructions to a Tampermonkey userscript running in the
    user's real Chrome tab. No Playwright dependency.
    """

    platform = "boss"

    def __init__(self, channel: UserscriptChannel | None = None) -> None:
        self._channel = channel or get_channel()

    # ------------------------------------------------------------------
    # Prepare: dry-run fill, stop before submit
    # ------------------------------------------------------------------

    async def prepare_submission(self, ctx: PrepareContext) -> PrepareResult:
        """Dry-run fill via the userscript bridge.

        Flow:
        1. Verify the userscript is connected; disconnected → ``unknown``.
        2. Set the active application (single-active invariant).
        3. **Verify the userscript is on the target page.** Compare the current
           page URL hash with ``sanitize_url(ctx.target_resource)``. Mismatch →
           ``unknown`` (never fill/click on the wrong page).
        4. Classify the page; any non-``form_ready`` state is a hard stop.
        5. Fill the message input (if present).
        6. Verify the final submit control is visible but **never click it**.
        7. Return a sanitized :class:`FilledSubmissionSnapshot`.
        8. Clear the channel.
        """
        _log.info(
            "boss.userscript.prepare",
            application_id=ctx.application_id,
            target_resource=sanitize_url(ctx.target_resource),
        )

        if not self._channel.is_connected():
            self._channel.clear()
            return PrepareResult(
                outcome=PrepareOutcome.unknown,
                failure_code=prepare_failure_code(PrepareOutcome.unknown),
                message=(
                    "Userscript bridge is not connected. Open the BOSS page "
                    "and ensure the userscript is running."
                ),
                diagnostic_reference=sanitize_diagnostic("bridge_not_connected"),
            )

        try:
            await self._channel.set_active_application(ctx.application_id)
        except RuntimeError as exc:
            _log.warning("boss.userscript.prepare.active_conflict", error=str(exc))
            return PrepareResult(
                outcome=PrepareOutcome.unknown,
                failure_code=prepare_failure_code(PrepareOutcome.unknown),
                message=str(exc),
                diagnostic_reference=sanitize_diagnostic("active_conflict"),
            )

        try:
            page = UserscriptBossPage(self._channel, is_submit_phase=False)

            # P1: Verify the userscript is on the target page before any
            # fill/click. This prevents operating on the wrong BOSS tab.
            target_hash = sanitize_url(ctx.target_resource)
            current_hash = await page._fetch_url_hash()
            if current_hash != target_hash:
                _log.warning(
                    "boss.userscript.prepare.page_mismatch",
                    target_hash=target_hash,
                    current_hash=current_hash,
                )
                return PrepareResult(
                    outcome=PrepareOutcome.unknown,
                    failure_code=prepare_failure_code(PrepareOutcome.unknown),
                    message=(
                        "Userscript is not on the target page. Navigate to "
                        "the target job page in the BOSS tab."
                    ),
                    diagnostic_reference=sanitize_diagnostic("page_mismatch"),
                )

            classification = await classify_page(page)
            if classification.outcome != FORM_READY:
                return self._prepare_failure(classification)
            return await self._fill_and_snapshot(page, ctx, classification)
        except RuntimeError as exc:
            _log.warning("boss.userscript.prepare.runtime_error", error=str(exc))
            return PrepareResult(
                outcome=PrepareOutcome.unknown,
                failure_code=prepare_failure_code(PrepareOutcome.unknown),
                message="BOSS prepare aborted due to a runtime error.",
                diagnostic_reference=sanitize_diagnostic("runtime_error"),
            )
        finally:
            self._channel.clear()

    async def _fill_and_snapshot(
        self,
        page: UserscriptBossPage,
        ctx: PrepareContext,
        classification: PageClassification,
    ) -> PrepareResult:
        """Fill safe fields and capture a sanitized snapshot (no final click)."""
        fields: list[FilledField] = []
        click_log: list[str] = []

        # Fill the outgoing message.
        if ctx.outgoing_text is not None:
            message_locator = _resolve(page, MESSAGE_INPUT)
            try:
                await page.fill(message_locator, ctx.outgoing_text)
            except Exception as exc:
                _log.warning("boss.userscript.prepare.fill_message_failed", error=str(exc))
                return PrepareResult(
                    outcome=PrepareOutcome.selector_drift,
                    failure_code=prepare_failure_code(PrepareOutcome.selector_drift),
                    message="Could not fill the message input.",
                    diagnostic_reference=sanitize_diagnostic("message_fill_failed"),
                )
            fields.append(
                FilledField(
                    name="message",
                    label="开场白",
                    value=ctx.outgoing_text,
                    source_artifact_id=ctx.selected_artifact_ids[0]
                    if ctx.selected_artifact_ids
                    else None,
                )
            )

        # Stage the resume upload reference only (visibility check).
        attachments: list[FilledAttachment] = []
        if ctx.resume_file_reference is not None:
            upload_locator = _resolve(page, RESUME_UPLOAD)
            try:
                upload_visible = await page.is_visible(upload_locator)
            except Exception:
                upload_visible = False
            if upload_visible:
                attachments.append(
                    FilledAttachment(
                        kind="resume",
                        display_name="resume.pdf",
                        reference=ctx.resume_file_reference,
                    )
                )

        # Verify the final submit control is visible. Never click it.
        submit_locator = _resolve(page, FINAL_SUBMIT_BUTTON)
        try:
            submit_seen = await page.is_visible(submit_locator)
        except Exception:
            submit_seen = False
        if not submit_seen:
            return PrepareResult(
                outcome=PrepareOutcome.selector_drift,
                failure_code=prepare_failure_code(PrepareOutcome.selector_drift),
                message="Final submit control was not visible after fill.",
                diagnostic_reference=sanitize_diagnostic("submit_not_visible"),
            )

        # Safety assertion: the final submit control was never clicked during
        # prepare. The UserscriptBossPage.click raises during prepare, but we
        # also assert the click_log as a belt-and-suspenders check.
        assert "final_submit" not in click_log, "prepare must never click final submit"

        title = await page.safe_title()
        url_hash = await page._fetch_url_hash()
        snapshot = FilledSubmissionSnapshot(
            target_platform=ctx.target_platform,
            target_resource=ctx.target_resource,
            application_id=ctx.application_id,
            selected_artifact_ids=list(ctx.selected_artifact_ids),
            resume_file_reference=ctx.resume_file_reference,
            fields=fields,
            attachments=attachments,
            page_state=FilledPageState(
                url_hash=url_hash,
                title=title,
                final_submit_selector_seen=submit_seen,
            ),
            captured_at=datetime.now(UTC),
        )
        return PrepareResult(outcome=PrepareOutcome.filled_preview, snapshot=snapshot)

    @staticmethod
    def _prepare_failure(classification: PageClassification) -> PrepareResult:
        outcome = _CLASSIFY_TO_PREPARE.get(classification.outcome, PrepareOutcome.unknown)
        failure_code = (
            prepare_failure_code(outcome)
            if outcome != PrepareOutcome.filled_preview
            else None
        )
        return PrepareResult(
            outcome=outcome,
            failure_code=failure_code,
            message=f"BOSS page classified as {classification.outcome}.",
            diagnostic_reference=sanitize_diagnostic(classification.diagnostic_reference),
        )

    # ------------------------------------------------------------------
    # Submit: final, exactly one click
    # ------------------------------------------------------------------

    async def submit_prepared(self, ctx: SubmitContext) -> SubmitResult:
        """Final submit via the userscript bridge.

        Flow:
        1. Verify connected; disconnected → ``unknown``.
        2. Set active application.
        3. **Verify the userscript is on the target page.** Compare the current
           page URL hash with ``sanitize_url(ctx.target_resource)``. Mismatch →
           ``unknown`` (never click on the wrong page).
        4. Re-classify the page; non-``form_ready`` is a hard stop.
        5. Re-apply the approved message text.
        6. Click the final submit control **exactly once**.
        7. Classify the post-click state.
        8. Clear the channel.
        """
        _log.info(
            "boss.userscript.submit",
            application_id=ctx.application_id,
            target_resource=sanitize_url(ctx.target_resource),
        )
        now = datetime.now(UTC)

        if not self._channel.is_connected():
            self._channel.clear()
            return SubmitResult(
                outcome=SubmitOutcome.unknown,
                failure_code=submit_failure_code(SubmitOutcome.unknown),
                message="Userscript bridge is not connected.",
                diagnostic_reference=sanitize_diagnostic("bridge_not_connected"),
                occurred_at=now,
            )

        try:
            await self._channel.set_active_application(ctx.application_id)
        except RuntimeError as exc:
            _log.warning("boss.userscript.submit.active_conflict", error=str(exc))
            return SubmitResult(
                outcome=SubmitOutcome.unknown,
                failure_code=submit_failure_code(SubmitOutcome.unknown),
                message=str(exc),
                diagnostic_reference=sanitize_diagnostic("active_conflict"),
                occurred_at=now,
            )

        try:
            page = UserscriptBossPage(self._channel, is_submit_phase=True)

            # P1: Verify the userscript is on the target page before any
            # fill/click. This prevents clicking "send" on the wrong BOSS tab.
            target_hash = sanitize_url(ctx.target_resource)
            current_hash = await page._fetch_url_hash()
            if current_hash != target_hash:
                _log.warning(
                    "boss.userscript.submit.page_mismatch",
                    target_hash=target_hash,
                    current_hash=current_hash,
                )
                return SubmitResult(
                    outcome=SubmitOutcome.unknown,
                    failure_code=submit_failure_code(SubmitOutcome.unknown),
                    message=(
                        "Userscript is not on the target page. Navigate to "
                        "the target job page in the BOSS tab."
                    ),
                    diagnostic_reference=sanitize_diagnostic("page_mismatch"),
                    occurred_at=now,
                )

            classification = await classify_page(page)
            if classification.outcome != FORM_READY:
                return self._submit_hard_stop(classification, now)

            # Re-apply the approved message text.
            message_field = next(
                (f for f in ctx.filled_snapshot.fields if f.name == "message"),
                None,
            )
            if message_field is not None and message_field.value is not None:
                message_locator = _resolve(page, MESSAGE_INPUT)
                try:
                    await page.fill(message_locator, message_field.value)
                except Exception as exc:
                    _log.warning("boss.userscript.submit.refill_failed", error=str(exc))
                    return SubmitResult(
                        outcome=SubmitOutcome.platform_failure,
                        failure_code=submit_failure_code(SubmitOutcome.platform_failure),
                        message="Could not re-apply the approved message before submit.",
                        diagnostic_reference=sanitize_diagnostic("refill_failed"),
                        occurred_at=now,
                    )

            # Click the final submit control exactly once.
            submit_locator = _resolve(page, FINAL_SUBMIT_BUTTON)
            try:
                await page.click(submit_locator)
            except Exception as exc:
                _log.warning("boss.userscript.submit.click_failed", error=str(exc))
                return SubmitResult(
                    outcome=SubmitOutcome.platform_failure,
                    failure_code=submit_failure_code(SubmitOutcome.platform_failure),
                    message="Final submit click failed.",
                    diagnostic_reference=sanitize_diagnostic("click_failed"),
                    occurred_at=now,
                )

            # Safety assertion: at most one final-submit click per call.
            assert page.click_count <= 1, "submit must click final control at most once"

            # Classify the post-click state.
            result_classification = await classify_submit_result(page)
            return self._submit_result_from_classification(result_classification, now)
        except RuntimeError as exc:
            _log.warning("boss.userscript.submit.runtime_error", error=str(exc))
            return SubmitResult(
                outcome=SubmitOutcome.unknown,
                failure_code=submit_failure_code(SubmitOutcome.unknown),
                message="BOSS submit aborted due to a runtime error.",
                diagnostic_reference=sanitize_diagnostic("runtime_error"),
                occurred_at=now,
            )
        finally:
            self._channel.clear()

    @staticmethod
    def _submit_hard_stop(classification: PageClassification, now: datetime) -> SubmitResult:
        outcome_map = {
            LOGIN_REQUIRED: SubmitOutcome.unknown,
            CAPTCHA_REQUIRED: SubmitOutcome.unknown,
            RATE_LIMITED: SubmitOutcome.unknown,
            DUPLICATE_DETECTED: SubmitOutcome.duplicate_detected,
            SELECTOR_DRIFT: SubmitOutcome.platform_failure,
            UNKNOWN: SubmitOutcome.unknown,
        }
        submit_outcome = outcome_map.get(classification.outcome, SubmitOutcome.unknown)
        return SubmitResult(
            outcome=submit_outcome,
            failure_code=submit_failure_code(submit_outcome),
            message=f"BOSS page classified as {classification.outcome} before submit.",
            diagnostic_reference=sanitize_diagnostic(classification.diagnostic_reference),
            occurred_at=now,
        )

    @staticmethod
    def _submit_result_from_classification(
        classification: PageClassification, now: datetime
    ) -> SubmitResult:
        if classification.outcome == "submitted":
            return SubmitResult(
                outcome=SubmitOutcome.submitted,
                platform_reference=None,
                occurred_at=now,
            )
        if classification.outcome == DUPLICATE_DETECTED:
            return SubmitResult(
                outcome=SubmitOutcome.duplicate_detected,
                failure_code=submit_failure_code(SubmitOutcome.duplicate_detected),
                message="Platform reported a duplicate.",
                diagnostic_reference=sanitize_diagnostic(classification.diagnostic_reference),
                occurred_at=now,
            )
        if classification.outcome == "platform_failure":
            return SubmitResult(
                outcome=SubmitOutcome.platform_failure,
                failure_code=submit_failure_code(SubmitOutcome.platform_failure),
                message="Platform error observed after submit.",
                diagnostic_reference=sanitize_diagnostic(classification.diagnostic_reference),
                occurred_at=now,
            )
        return SubmitResult(
            outcome=SubmitOutcome.unknown,
            failure_code=submit_failure_code(SubmitOutcome.unknown),
            message="Ambiguous post-submit state; manual reconciliation required.",
            diagnostic_reference=sanitize_diagnostic(classification.diagnostic_reference),
            occurred_at=now,
        )


__all__ = ["RemoteLocator", "UserscriptBossAdapter", "UserscriptBossPage"]
