"""Manual platform adapter (generate-and-copy delivery path).

First-class member of the :class:`~app.platforms.base.PlatformAdapter` family:

- :meth:`prepare_submission` builds the same sanitized
  :class:`~app.platforms.base.FilledSubmissionSnapshot` the automated adapters
  produce, entirely in memory — no browser, no session, no network. The
  preview is exactly what the user copies and pastes.
- :meth:`submit_prepared` / :meth:`execute_communication` deliberately perform
  **no** platform action: the manual contract delegates the final paste to the
  user. Both return an ``unknown`` outcome carrying the stable
  ``manual_handoff`` failure code so workflows can distinguish "designed
  human hand-off" from real platform ambiguity and never retry automatically.

Safety invariants preserved from the automated adapters: no secrets, cookies,
or raw page data in any result; nothing persisted here (persistence stays in
the calling service, same as every other adapter).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.platforms.base import (
    CommunicationExecuteContext,
    CommunicationExecuteResult,
    CommunicationOutcome,
    FilledField,
    FilledPageState,
    FilledSubmissionSnapshot,
    PrepareContext,
    PrepareOutcome,
    PrepareResult,
    SubmitContext,
    SubmitOutcome,
    SubmitResult,
)

_log = get_logger("app.platforms.manual.adapter")

#: Stable envelope code marking a designed human hand-off. Workflows must route
#: this to manual guidance (copy → paste), never to automatic retry.
MANUAL_HANDOFF_CODE = "manual_handoff"

#: Message length cap mirrored from the automated adapters' outgoing-text
#: validation, so an oversized payload is rejected identically on every path.
_MAX_MESSAGE_LEN = 2000


class ManualPlatformAdapter:
    """Adapter for the platform-agnostic generate-and-copy path.

    Suitable for *any* job platform: the generated artifacts are plain text
    the user pastes by hand. Selected whenever the application's platform has
    no dedicated automation adapter (see
    :func:`app.platforms.get_adapter_for_platform`).
    """

    platform = "manual"

    async def prepare_submission(self, ctx: PrepareContext) -> PrepareResult:
        """Build an in-memory filled preview from the approved outgoing text.

        Always succeeds when the input is valid: there is no page to classify,
        no login to check, and no selector to drift. The snapshot carries the
        outgoing message as the single approvable field, mirroring the shape
        automated adapters return.
        """
        _log.info(
            "manual.adapter.prepare",
            application_id=ctx.application_id,
            target_platform=ctx.target_platform,
        )
        fields: list[FilledField] = []
        if ctx.outgoing_text is not None:
            if len(ctx.outgoing_text) > _MAX_MESSAGE_LEN:
                return PrepareResult(
                    outcome=PrepareOutcome.unknown,
                    failure_code="message_too_long",
                    message=(
                        f"Outgoing text exceeds {_MAX_MESSAGE_LEN} characters; "
                        "regenerate a shorter opening message."
                    ),
                )
            fields.append(
                FilledField(
                    name="message",
                    label="开场白",
                    value=ctx.outgoing_text,
                    source_artifact_id=(
                        ctx.selected_artifact_ids[0]
                        if ctx.selected_artifact_ids
                        else None
                    ),
                )
            )
        snapshot = FilledSubmissionSnapshot(
            target_platform=ctx.target_platform,
            target_resource=ctx.target_resource,
            application_id=ctx.application_id,
            selected_artifact_ids=ctx.selected_artifact_ids,
            resume_file_reference=ctx.resume_file_reference,
            fields=fields,
            attachments=[],
            page_state=FilledPageState(),
            captured_at=datetime.now(UTC),
        )
        return PrepareResult(outcome=PrepareOutcome.filled_preview, snapshot=snapshot)

    async def submit_prepared(self, ctx: SubmitContext) -> SubmitResult:
        """Never submits. The manual contract delegates the paste to the user.

        Returns ``unknown`` + ``manual_handoff`` so the workflow records a
        designed hand-off instead of an error, and never auto-retries. The
        user confirms delivery via the application status ("标记已投递").
        """
        _log.info(
            "manual.adapter.submit.handoff",
            application_id=ctx.application_id,
            target_platform=ctx.target_platform,
        )
        return SubmitResult(
            outcome=SubmitOutcome.unknown,
            failure_code=MANUAL_HANDOFF_CODE,
            message=(
                "Manual delivery: copy the approved content and paste it on "
                "the platform yourself, then mark the application as "
                "submitted. The system never auto-submits in manual mode."
            ),
            occurred_at=datetime.now(UTC),
        )

    async def execute_communication(
        self, ctx: CommunicationExecuteContext
    ) -> CommunicationExecuteResult:
        """Never sends. The user copies the opening message and sends it by hand.

        Same hand-off contract as :meth:`submit_prepared`: ``unknown`` +
        ``manual_handoff``, no automatic retry.
        """
        _log.info(
            "manual.adapter.communicate.handoff",
            application_id=ctx.application_id,
            target_platform=ctx.target_platform,
        )
        return CommunicationExecuteResult(
            outcome=CommunicationOutcome.unknown,
            failure_code=MANUAL_HANDOFF_CODE,
            message=(
                "Manual delivery: copy the opening message and send it on "
                "the platform yourself. The system never auto-sends in "
                "manual mode."
            ),
            occurred_at=datetime.now(UTC),
        )


__all__ = ["MANUAL_HANDOFF_CODE", "ManualPlatformAdapter"]
