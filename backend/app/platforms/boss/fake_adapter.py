"""Fake BOSS adapter for deterministic tests.

This adapter implements :class:`~app.platforms.base.PlatformAdapter` without any
browser automation. Its behavior is driven by a small injected ``scenario``
field so tests can exercise every prepare/submit outcome (filled preview, login
required, CAPTCHA, selector drift, rate limit, duplicate, upload failure,
unknown) deterministically.

The real BOSS Web adapter (Playwright-backed) lives in
:mod:`app.platforms.boss.adapter` and is enabled only behind an explicit
environment flag. Product services select the adapter via
:func:`app.platforms.boss.registry.get_adapter`.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
)


class FakeBossAdapter:
    """In-memory :class:`PlatformAdapter` for tests.

    ``scenario`` selects which outcome the next ``prepare_submission`` /
    ``submit_prepared`` call returns. ``snapshot_override`` lets a test inject a
    pre-built snapshot for the ``filled_preview`` path; otherwise a minimal
    sanitized snapshot is synthesized from the context.
    """

    platform = "boss"

    def __init__(
        self,
        *,
        scenario: str = "filled_preview",
        snapshot_override: FilledSubmissionSnapshot | None = None,
        platform_reference: str | None = "boss-ref-123",
        message: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.snapshot_override = snapshot_override
        self.platform_reference = platform_reference
        self.message = message
        self.prepare_calls: list[PrepareContext] = []
        self.submit_calls: list[SubmitContext] = []
        self.communicate_calls: list[CommunicationExecuteContext] = []

    # Scenarios that are valid SubmitOutcome values but NOT valid PrepareOutcome
    # values. On prepare these still return a filled preview; on submit they
    # drive the failure classification. ``duplicate_detected`` and ``unknown``
    # exist on *both* enums, so the caller disambiguates with the ``prepare_`` /
    # ``submit_`` prefix.
    _SUBMIT_ONLY_SCENARIOS = {
        "submitted",
        "submit_duplicate_detected",
        "submit_unknown",
        "platform_failure",
    }

    async def prepare_submission(self, ctx: PrepareContext) -> PrepareResult:
        self.prepare_calls.append(ctx)
        snapshot = self.snapshot_override or self._build_snapshot(ctx)
        if self.scenario == "filled_preview" or self.scenario in self._SUBMIT_ONLY_SCENARIOS:
            return PrepareResult(outcome=PrepareOutcome.filled_preview, snapshot=snapshot)

        outcome = PrepareOutcome(self.scenario)
        from app.platforms.base import prepare_failure_code

        return PrepareResult(
            outcome=outcome,
            failure_code=prepare_failure_code(outcome),
            message=self.message or f"fake boss prepare: {self.scenario}",
        )

    async def submit_prepared(self, ctx: SubmitContext) -> SubmitResult:
        self.submit_calls.append(ctx)
        now = datetime.now(UTC)
        if self.scenario == "submitted":
            return SubmitResult(
                outcome=SubmitOutcome.submitted,
                platform_reference=self.platform_reference,
                occurred_at=now,
            )
        if self.scenario in ("duplicate_detected", "submit_duplicate_detected"):
            return SubmitResult(
                outcome=SubmitOutcome.duplicate_detected,
                failure_code="platform_duplicate_detected",
                message=self.message or "fake boss submit: duplicate",
                occurred_at=now,
            )
        if self.scenario in ("unknown", "submit_unknown"):
            return SubmitResult(
                outcome=SubmitOutcome.unknown,
                failure_code="platform_unknown_result",
                message=self.message or "fake boss submit: unknown",
                occurred_at=now,
            )
        # Default: platform_failure (covers login/captcha/drift/rate/upload).
        return SubmitResult(
            outcome=SubmitOutcome.platform_failure,
            failure_code="platform_failure",
            message=self.message or f"fake boss submit: {self.scenario}",
            occurred_at=now,
        )

    async def execute_communication(
        self, ctx: CommunicationExecuteContext
    ) -> CommunicationExecuteResult:
        """Scenario-driven immediate-communicate execute for tests.

        Recognized communicate scenarios:

        - ``communicate_succeeded`` → ``CommunicationOutcome.succeeded``
        - ``communicate_duplicate`` → ``CommunicationOutcome.duplicate``
        - ``communicate_failed`` → ``CommunicationOutcome.failed`` (with
          ``immediate_button_missing`` as the default failure code, or
          ``message_input_missing`` / ``send_result_unknown`` if the
          scenario string carries that suffix)
        - ``communicate_unknown`` → ``CommunicationOutcome.unknown``
        - default (no communicate scenario) → ``succeeded``

        The ``communicate_calls`` list tracks every invocation so tests can
        assert that the adapter was (or was not) called.
        """
        self.communicate_calls.append(ctx)
        now = datetime.now(UTC)

        if self.scenario == "communicate_duplicate":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.duplicate,
                failure_code=communication_failure_code(CommunicationOutcome.duplicate),
                message=self.message or "fake boss communicate: duplicate",
                occurred_at=now,
            )

        if self.scenario == "communicate_unknown":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.unknown,
                failure_code="send_result_unknown",
                message=self.message or "fake boss communicate: unknown",
                occurred_at=now,
            )

        if self.scenario == "communicate_failed":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.failed,
                failure_code="immediate_button_missing",
                message=self.message or "fake boss communicate: failed",
                occurred_at=now,
            )

        if self.scenario == "communicate_failed_message_input":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.failed,
                failure_code="message_input_missing",
                message=self.message or "fake boss communicate: message input missing",
                occurred_at=now,
            )

        if self.scenario == "communicate_failed_send_unknown":
            return CommunicationExecuteResult(
                outcome=CommunicationOutcome.unknown,
                failure_code="send_result_unknown",
                message=self.message or "fake boss communicate: send result unknown",
                occurred_at=now,
            )

        # Default (including "communicate_succeeded" and any non-communicate
        # scenario): return succeeded.
        return CommunicationExecuteResult(
            outcome=CommunicationOutcome.succeeded,
            platform_reference=self.platform_reference,
            occurred_at=now,
        )

    @staticmethod
    def _build_snapshot(ctx: PrepareContext) -> FilledSubmissionSnapshot:
        fields: list[FilledField] = []
        if ctx.outgoing_text is not None:
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
        attachments: list[FilledAttachment] = []
        if ctx.resume_file_reference is not None:
            attachments.append(
                FilledAttachment(
                    kind="resume",
                    display_name="resume.pdf",
                    reference=ctx.resume_file_reference,
                )
            )
        return FilledSubmissionSnapshot(
            target_platform=ctx.target_platform,
            target_resource=ctx.target_resource,
            application_id=ctx.application_id,
            selected_artifact_ids=list(ctx.selected_artifact_ids),
            resume_file_reference=ctx.resume_file_reference,
            fields=fields,
            attachments=attachments,
            page_state=FilledPageState(
                url_hash=f"sha256:{ctx.target_resource[:8]}",
                title="BOSS application form",
                final_submit_selector_seen=True,
            ),
            captured_at=datetime.now(UTC),
        )
