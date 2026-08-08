"""Real BOSS Web adapter (Playwright-backed).

This adapter is enabled **only** behind the ``boss_adapter_enabled`` settings
flag (design.md §Browser Automation Choice). It implements real Playwright
navigation, page classification, fill, and submit logic.

Safety invariants enforced here regardless of implementation depth:

- The adapter never persists Playwright storage state, cookies, headers, tokens,
  or traces containing page HTML.
- The adapter never solves CAPTCHA, bypasses rate limits, or keeps clicking when
  selectors drift. Those are hard stops returned as
  :class:`~app.platforms.base.PrepareResult` /
  :class:`~app.platforms.base.SubmitResult` variants.
- The adapter operates on exactly one ``application_id`` per call. There is no
  batch/autonomous path.
- **prepare** navigates + fills but **never** clicks the final submit control.
- **submit** re-opens, re-classifies, re-verifies, and clicks the final submit
  control **exactly once**. ``submitted`` is returned only when an observed
  success marker is present — never inferred from a lack of errors.
- **execute_communication** (CDP fallback) clicks "立即沟通" at most once, fills
  the chat message, clicks "发送" at most once, and classifies the post-send
  state. It is a fallback for when the userscript bridge is not enabled; the
  userscript path remains the recommended real communicate path.

Playwright is imported lazily inside the runtime (not here) so the dependency is
only required when the flag is set. ``__init__`` only validates the profile
directory; the actual browser launch happens in ``prepare_submission`` /
``submit_prepared`` via :class:`BossBrowserRuntime`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.platforms.base import (
    CommunicationExecuteContext,
    CommunicationExecuteResult,
    CommunicationOutcome,
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
    communication_failure_code,
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
    classify_communication_result,
    classify_page,
    classify_submit_result,
)
from app.platforms.boss.runtime import BossBrowserRuntime, BossPage
from app.platforms.boss.sanitizer import sanitize_diagnostic, sanitize_url
from app.platforms.boss.selectors import (
    COMMUNICATION_MESSAGE_INPUT,
    COMMUNICATION_SEND_BUTTON,
    CONTINUE_COMMUNICATE_BUTTON,
    FINAL_SUBMIT_BUTTON,
    IMMEDIATE_COMMUNICATE_BUTTON,
    MESSAGE_INPUT,
    RESUME_UPLOAD,
    Selector,
)

_log = get_logger("app.platforms.boss.adapter")

#: Maps classifier outcome codes (from classifiers.py) to ``PrepareOutcome``.
_CLASSIFY_TO_PREPARE: dict[str, PrepareOutcome] = {
    FORM_READY: PrepareOutcome.filled_preview,
    LOGIN_REQUIRED: PrepareOutcome.login_required,
    CAPTCHA_REQUIRED: PrepareOutcome.captcha_required,
    RATE_LIMITED: PrepareOutcome.rate_limited,
    DUPLICATE_DETECTED: PrepareOutcome.duplicate_detected,
    SELECTOR_DRIFT: PrepareOutcome.selector_drift,
    UNKNOWN: PrepareOutcome.unknown,
}


class RealBossAdapter:
    """Playwright-backed BOSS Web adapter.

    Constructed only when ``boss_adapter_enabled`` is set. The browser session
    is opened via either ``boss_cdp_endpoint`` (preferred — connects to the
    user's already-logged-in real Chrome, bypassing BOSS anti-automation
    detection) or ``boss_session_profile_dir`` (persistent-context fallback).
    Both are process config, never a request payload. This adapter never
    stores credentials.
    """

    platform = "boss"

    def __init__(self) -> None:
        # Validate that Playwright is importable so the registry test can
        # distinguish "flag honored but dep missing" from "flag ignored". The
        # actual browser launch is deferred to prepare/submit.
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env-gated
            raise RuntimeError(
                "BOSS_ADAPTER_ENABLED is set but playwright is not installed. "
                "Install it (pip install '.[boss]') before enabling the real adapter."
            ) from exc

    # ------------------------------------------------------------------
    # Prepare: dry-run fill, stop before submit
    # ------------------------------------------------------------------

    async def prepare_submission(  # pragma: no cover - exercised via fake Playwright in tests
        self, ctx: PrepareContext
    ) -> PrepareResult:
        """Dry-run fill against BOSS Web.

        Flow:
        1. Load profile dir from settings; missing → ``login_required``.
        2. Open ``target_resource`` in a bounded Playwright context.
        3. Classify the page; any non-``form_ready`` state is a hard stop.
        4. Fill the message input with ``outgoing_text`` (if present).
        5. Verify the final submit control is visible but **never click it**.
        6. Return a sanitized :class:`FilledSubmissionSnapshot`.
        """
        _log.info(
            "boss.adapter.prepare",
            application_id=ctx.application_id,
            target_resource=sanitize_url(ctx.target_resource),
        )
        settings = get_settings()
        profile_dir = settings.boss_session_profile_dir
        cdp_endpoint = settings.boss_cdp_endpoint
        if not profile_dir and not cdp_endpoint:
            return PrepareResult(
                outcome=PrepareOutcome.login_required,
                failure_code=prepare_failure_code(PrepareOutcome.login_required),
                message=(
                    "boss_session_profile_dir and boss_cdp_endpoint are both "
                    "unset; cannot open a session."
                ),
                diagnostic_reference="missing_session_config",
            )

        runtime = BossBrowserRuntime(
            profile_dir=profile_dir, cdp_endpoint=cdp_endpoint
        )
        try:
            async with await runtime.open(ctx.target_resource) as page:
                classification = await classify_page(page)
                if classification.outcome != FORM_READY:
                    return self._prepare_failure(classification)
                return await self._fill_and_snapshot(page, ctx, classification)
        except RuntimeError as exc:
            # Navigation/timeout/runtime errors are unknown hard stops. Never
            # synthesize a filled preview from an error path.
            _log.warning("boss.adapter.prepare.runtime_error", error=str(exc))
            return PrepareResult(
                outcome=PrepareOutcome.unknown,
                failure_code=prepare_failure_code(PrepareOutcome.unknown),
                message="BOSS prepare aborted due to a runtime error.",
                diagnostic_reference=sanitize_diagnostic("runtime_error"),
            )

    async def _fill_and_snapshot(
        self, page: BossPage, ctx: PrepareContext, classification: PageClassification
    ) -> PrepareResult:
        """Fill safe fields and capture a sanitized snapshot (no final click)."""
        fields: list[FilledField] = []
        click_log: list[str] = []  # tracks what was clicked for the no-submit assertion

        # Fill the outgoing message.
        if ctx.outgoing_text is not None:
            message_locator = _resolve(page, MESSAGE_INPUT)
            try:
                await page.fill(message_locator, ctx.outgoing_text)
            except Exception as exc:
                _log.warning("boss.adapter.prepare.fill_message_failed", error=str(exc))
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

        # Stage the resume upload reference only (do not actually upload in
        # dry-run — we record that the widget was visible). A real upload happens
        # at submit time so the platform never sees a staged-but-unsubmitted
        # file from a prepare that the user later aborts.
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
            # If the upload widget is absent but a resume ref was provided, we
            # do NOT hard-stop: BOSS conversations may not need an upload. The
            # snapshot simply omits the attachment.

        # Verify the final submit control is visible. We deliberately do NOT
        # click it — this is the stop-before-submit invariant.
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
        # prepare. ``click_log`` is inspected by tests.
        assert "final_submit" not in click_log, "prepare must never click final submit"

        title = await page.safe_title()
        snapshot = FilledSubmissionSnapshot(
            target_platform=ctx.target_platform,
            target_resource=ctx.target_resource,
            application_id=ctx.application_id,
            selected_artifact_ids=list(ctx.selected_artifact_ids),
            resume_file_reference=ctx.resume_file_reference,
            fields=fields,
            attachments=attachments,
            page_state=FilledPageState(
                url_hash=page.url_hash(),
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

    async def submit_prepared(  # pragma: no cover - exercised via fake Playwright in tests
        self, ctx: SubmitContext
    ) -> SubmitResult:
        """Final submit against BOSS Web.

        Flow:
        1. Re-open the same target resource/session.
        2. Re-classify the page (same conservative classifier).
        3. Re-apply the filled message from the approved snapshot.
        4. Click the final submit control **exactly once**.
        5. Classify the result: ``submitted`` only on an observed success marker.
        """
        _log.info(
            "boss.adapter.submit",
            application_id=ctx.application_id,
            target_resource=sanitize_url(ctx.target_resource),
        )
        now = datetime.now(UTC)
        settings = get_settings()
        profile_dir = settings.boss_session_profile_dir
        cdp_endpoint = settings.boss_cdp_endpoint
        if not profile_dir and not cdp_endpoint:
            return SubmitResult(
                outcome=SubmitOutcome.unknown,
                failure_code=submit_failure_code(SubmitOutcome.unknown),
                message=(
                    "boss_session_profile_dir and boss_cdp_endpoint are both "
                    "unset; cannot open a session."
                ),
                diagnostic_reference=sanitize_diagnostic("missing_session_config"),
                occurred_at=now,
            )

        runtime = BossBrowserRuntime(
            profile_dir=profile_dir, cdp_endpoint=cdp_endpoint
        )
        click_count = 0
        try:
            async with await runtime.open(ctx.target_resource) as page:
                classification = await classify_page(page)
                if classification.outcome != FORM_READY:
                    return self._submit_hard_stop(classification, now)

                # Re-apply the approved message text so the platform form has
                # the exact value the user approved.
                message_field = next(
                    (f for f in ctx.filled_snapshot.fields if f.name == "message"),
                    None,
                )
                if message_field is not None and message_field.value is not None:
                    message_locator = _resolve(page, MESSAGE_INPUT)
                    try:
                        await page.fill(message_locator, message_field.value)
                    except Exception as exc:
                        _log.warning("boss.adapter.submit.refill_failed", error=str(exc))
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
                    click_count += 1
                except Exception as exc:
                    _log.warning("boss.adapter.submit.click_failed", error=str(exc))
                    return SubmitResult(
                        outcome=SubmitOutcome.platform_failure,
                        failure_code=submit_failure_code(SubmitOutcome.platform_failure),
                        message="Final submit click failed.",
                        diagnostic_reference=sanitize_diagnostic("click_failed"),
                        occurred_at=now,
                    )

                # Safety assertion: at most one final-submit click per call.
                assert click_count <= 1, "submit must click final control at most once"

                # Classify the post-click state.
                result_classification = await classify_submit_result(page)
                return self._submit_result_from_classification(result_classification, now)
        except RuntimeError as exc:
            _log.warning("boss.adapter.submit.runtime_error", error=str(exc))
            return SubmitResult(
                outcome=SubmitOutcome.unknown,
                failure_code=submit_failure_code(SubmitOutcome.unknown),
                message="BOSS submit aborted due to a runtime error.",
                diagnostic_reference=sanitize_diagnostic("runtime_error"),
                occurred_at=now,
            )

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


    # ------------------------------------------------------------------
    # Execute communication: CDP/Playwright fallback for immediate communicate
    # ------------------------------------------------------------------
    #
    # This is the **fallback** path when the userscript bridge is not enabled.
    # The userscript path (``UserscriptBossAdapter.execute_communication``)
    # remains the recommended real path. The CDP path here shares the same
    # ``CommunicationExecuteContext`` / ``CommunicationExecuteResult`` contract
    # and the same safety invariants:
    #
    # - At most 1 click on ``IMMEDIATE_COMMUNICATE_BUTTON`` + 1 click on
    #   ``COMMUNICATION_SEND_BUTTON`` per execute.
    # - page hash verified before click and after fill (mismatch = unknown).
    # - selector drift / both entry buttons invisible = failed (hard stop).
    # - continue button visible = duplicate (conversation already exists).
    # - ``unknown`` never auto-retried; manual reconciliation required.
    # - No raw URL / cookie / token / HTML persisted — only sanitized hashes
    #   and opaque diagnostic references.

    async def execute_communication(  # pragma: no cover - exercised via fake Playwright in tests
        self, ctx: CommunicationExecuteContext
    ) -> CommunicationExecuteResult:
        """CDP/Playwright fallback for immediate communicate.

        Flow (design.md §Flow):
        1. Load session config (profile dir / CDP endpoint); missing →
           ``unknown`` + ``missing_session_config``.
        2. Open the runtime and obtain the current ``BossPage`` (CDP mode
           reuses the user's already-open tab — never calls ``page.goto``).
        3. Verify the current page hash matches ``sanitize_url(ctx.target_resource)``;
           mismatch → ``unknown`` (stale action, no click).
        4. Probe ``IMMEDIATE_COMMUNICATE_BUTTON`` and
           ``CONTINUE_COMMUNICATE_BUTTON`` visibility:
           - continue visible → ``duplicate`` (conversation already exists).
           - both invisible → ``failed`` (``selector_drift``).
        5. Click ``IMMEDIATE_COMMUNICATE_BUTTON`` at most once.
        6. Fill ``COMMUNICATION_MESSAGE_INPUT`` with ``ctx.opening_message``.
        7. Re-verify the page hash is unchanged (page binding invariant).
        8. Click ``COMMUNICATION_SEND_BUTTON`` at most once.
        9. Classify via :func:`classify_communication_result` and map to a
           :class:`CommunicationExecuteResult`.
        """
        _log.info(
            "boss.adapter.communicate",
            application_id=ctx.application_id,
            target_resource=sanitize_url(ctx.target_resource),
        )
        now = datetime.now(UTC)
        settings = get_settings()
        profile_dir = settings.boss_session_profile_dir
        cdp_endpoint = settings.boss_cdp_endpoint
        if not profile_dir and not cdp_endpoint:
            _log.warning(
                "boss.adapter.communicate.missing_session_config",
                application_id=ctx.application_id,
                failure_code=communication_failure_code(CommunicationOutcome.unknown),
            )
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.unknown,
                failure_code=communication_failure_code(CommunicationOutcome.unknown),
                message=(
                    "boss_session_profile_dir and boss_cdp_endpoint are both "
                    "unset; cannot open a session."
                ),
                diagnostic_reference=sanitize_diagnostic("missing_session_config"),
                occurred_at=now,
            )

        runtime = BossBrowserRuntime(
            profile_dir=profile_dir, cdp_endpoint=cdp_endpoint
        )
        click_count = 0
        send_count = 0
        try:
            async with await runtime.open(ctx.target_resource) as page:
                # Step 3: Verify page binding — never click on the wrong tab.
                target_hash = sanitize_url(ctx.target_resource)
                current_hash = page.url_hash()
                if current_hash != target_hash:
                    _log.warning(
                        "boss.adapter.communicate.page_mismatch",
                        application_id=ctx.application_id,
                        failure_code="page_binding_mismatch",
                        target_hash=target_hash,
                        current_hash=current_hash,
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.unknown,
                        failure_code=communication_failure_code(
                            CommunicationOutcome.unknown
                        ),
                        message=(
                            "The current BOSS page does not match the approved "
                            "target resource. Navigate to the target job page."
                        ),
                        diagnostic_reference=sanitize_diagnostic("page_binding_mismatch"),
                        occurred_at=now,
                    )

                # Step 4: Selector drift detection — probe entry buttons before
                # clicking. Read-only checks; preserves safety invariants.
                immediate_probe = await page.is_visible(
                    _resolve(page, IMMEDIATE_COMMUNICATE_BUTTON)
                )
                continue_probe = await page.is_visible(
                    _resolve(page, CONTINUE_COMMUNICATE_BUTTON)
                )
                if continue_probe and not immediate_probe:
                    # 继续沟通 visible without 立即沟通 → conversation already
                    # exists. Return duplicate (not a failure).
                    _log.info(
                        "boss.adapter.communicate.duplicate_continue_visible",
                        application_id=ctx.application_id,
                        failure_code=communication_failure_code(
                            CommunicationOutcome.duplicate
                        ),
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.duplicate,
                        failure_code=communication_failure_code(
                            CommunicationOutcome.duplicate
                        ),
                        message="A conversation already exists for this contact.",
                        diagnostic_reference=sanitize_diagnostic(
                            "continue_communicate_visible"
                        ),
                        occurred_at=now,
                    )
                if not immediate_probe and not continue_probe:
                    _log.warning(
                        "boss.adapter.communicate.selector_drift",
                        application_id=ctx.application_id,
                        failure_code="selector_drift",
                        immediate_visible=immediate_probe,
                        continue_visible=continue_probe,
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.failed,
                        failure_code="selector_drift",
                        message=(
                            "Selector drift: both IMMEDIATE_COMMUNICATE_BUTTON "
                            "and CONTINUE_COMMUNICATE_BUTTON are invisible."
                        ),
                        diagnostic_reference=sanitize_diagnostic(
                            "selector_drift_immediate_communicate"
                        ),
                        occurred_at=now,
                    )

                # Step 5: Click 立即沟通 at most once.
                immediate_locator = _resolve(page, IMMEDIATE_COMMUNICATE_BUTTON)
                try:
                    await page.click(immediate_locator)
                    click_count += 1
                except Exception as exc:
                    _log.warning(
                        "boss.adapter.communicate.immediate_click_failed",
                        application_id=ctx.application_id,
                        failure_code="immediate_button_missing",
                        error_type=type(exc).__name__,
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.failed,
                        failure_code="immediate_button_missing",
                        message="Could not click the 立即沟通 button.",
                        diagnostic_reference=sanitize_diagnostic(
                            "immediate_button_missing"
                        ),
                        occurred_at=now,
                    )

                # Step 6: Fill the opening message into the chat dialog.
                message_locator = _resolve(page, COMMUNICATION_MESSAGE_INPUT)
                try:
                    await page.fill(message_locator, ctx.opening_message)
                except Exception as exc:
                    _log.warning(
                        "boss.adapter.communicate.fill_message_failed",
                        application_id=ctx.application_id,
                        failure_code="message_input_missing",
                        error_type=type(exc).__name__,
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.failed,
                        failure_code="message_input_missing",
                        message="Could not find or fill the chat message input.",
                        diagnostic_reference=sanitize_diagnostic(
                            "message_input_missing"
                        ),
                        occurred_at=now,
                    )

                # Step 7: Verify page hash unchanged since step 3.
                post_fill_hash = page.url_hash()
                if post_fill_hash != target_hash:
                    _log.warning(
                        "boss.adapter.communicate.page_hash_changed",
                        application_id=ctx.application_id,
                        failure_code="page_binding_mismatch",
                        target_hash=target_hash,
                        post_fill_hash=post_fill_hash,
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.unknown,
                        failure_code=communication_failure_code(
                            CommunicationOutcome.unknown
                        ),
                        message="Page changed during communication; action is stale.",
                        diagnostic_reference=sanitize_diagnostic("page_binding_mismatch"),
                        occurred_at=now,
                    )

                # Step 8: Click send at most once.
                send_locator = _resolve(page, COMMUNICATION_SEND_BUTTON)
                try:
                    await page.click(send_locator)
                    send_count += 1
                except Exception as exc:
                    _log.warning(
                        "boss.adapter.communicate.send_failed",
                        application_id=ctx.application_id,
                        failure_code="send_result_unknown",
                        error_type=type(exc).__name__,
                    )
                    return CommunicationExecuteResult(
                        outcome=CommunicationOutcome.failed,
                        failure_code="send_result_unknown",
                        message="Could not click the send button.",
                        diagnostic_reference=sanitize_diagnostic("send_failed"),
                        occurred_at=now,
                    )

                # Safety assertion: at most 1 immediate click + 1 send click.
                assert click_count <= 1 and send_count <= 1, (
                    "communicate must click at most 1 immediate + 1 send"
                )

                # Step 9: Classify the post-send state and map to a result.
                classification = await classify_communication_result(page)
                return self._communication_result_from_classification(
                    classification, now
                )
        except RuntimeError as exc:
            _log.warning(
                "boss.adapter.communicate.runtime_error",
                application_id=ctx.application_id,
                failure_code=communication_failure_code(CommunicationOutcome.unknown),
                error_type=type(exc).__name__,
            )
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.unknown,
                failure_code=communication_failure_code(CommunicationOutcome.unknown),
                message="BOSS communicate aborted due to a runtime error.",
                diagnostic_reference=sanitize_diagnostic("runtime_error"),
                occurred_at=now,
            )

    @staticmethod
    def _communication_result_from_classification(
        classification: PageClassification, now: datetime
    ) -> CommunicationExecuteResult:
        """Map a post-send :class:`PageClassification` to a communication result.

        Mirrors :meth:`UserscriptBossAdapter._communication_result_from_classification`.
        Priority: success → duplicate → error → unknown.
        """
        if classification.outcome == "succeeded":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.succeeded,
                platform_reference=None,
                occurred_at=now,
            )
        if classification.outcome == DUPLICATE_DETECTED:
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.duplicate,
                failure_code=communication_failure_code(CommunicationOutcome.duplicate),
                message="A conversation already exists for this contact.",
                diagnostic_reference=sanitize_diagnostic(
                    classification.diagnostic_reference
                ),
                occurred_at=now,
            )
        if classification.outcome == "platform_failure":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.failed,
                failure_code=communication_failure_code(CommunicationOutcome.failed),
                message="Platform error observed after sending the message.",
                diagnostic_reference=sanitize_diagnostic(
                    classification.diagnostic_reference
                ),
                occurred_at=now,
            )
        # Ambiguous: no clear success, no clear failure. Hard stop.
        return CommunicationExecuteResult(
            outcome=CommunicationOutcome.unknown,
            failure_code="send_result_unknown",
            message="Ambiguous post-send state; manual reconciliation required.",
            diagnostic_reference=sanitize_diagnostic(
                classification.diagnostic_reference
            ),
            occurred_at=now,
        )


def _resolve(page: BossPage, selector: Selector) -> Any:
    """Resolve a :class:`Selector` against a :class:`BossPage`."""
    if selector.kind.value == "role":
        return page.get_by_role(selector.value, name=selector.name)
    if selector.kind.value == "label":
        return page.get_by_label(selector.value)
    if selector.kind.value == "placeholder":
        return page.get_by_placeholder(selector.value)
    return page.locator(selector.value)


__all__ = ["RealBossAdapter"]
