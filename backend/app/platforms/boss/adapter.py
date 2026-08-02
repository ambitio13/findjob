"""Real BOSS Web adapter (Playwright-backed).

This adapter is enabled **only** behind the ``BOSS_ADAPTER_ENABLED`` environment
flag (design.md §Browser Automation Choice). It is intentionally a thin shell
in this task: the pilot proves the safety contracts (approval boundary,
idempotency, stale-source, sanitized snapshots) with the fake adapter, and the
real browser automation is wired in a follow-up once the contracts are audited.

Safety invariants enforced here regardless of implementation depth:

- The adapter never persists Playwright storage state, cookies, headers, tokens,
  or traces containing page HTML.
- The adapter never solves CAPTCHA, bypasses rate limits, or keeps clicking when
  selectors drift. Those are hard stops returned as
  :class:`~app.platforms.base.PrepareResult` /
  :class:`~app.platforms.base.SubmitResult` variants.
- The adapter operates on exactly one ``application_id`` per call. There is no
  batch/autonomous path.

Playwright is imported lazily inside ``__init__`` so the dependency is only
required when the flag is set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
)

_log = get_logger("app.platforms.boss.adapter")


class RealBossAdapter:
    """Playwright-backed BOSS Web adapter.

    Constructed only when ``BOSS_ADAPTER_ENABLED`` is set. The browser session
    is provided by the caller via ``ctx.session_reference`` (an opaque handle to
    a user-provided session); this adapter never stores credentials.
    """

    platform = "boss"

    def __init__(self) -> None:
        # Lazy import so Playwright is only required when the real adapter is
        # actually enabled. The import is intentionally inside __init__ so a
        # missing dependency does not break default test/dev runs.
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env-gated
            raise RuntimeError(
                "BOSS_ADAPTER_ENABLED is set but playwright is not installed. "
                "Install it before enabling the real adapter."
            ) from exc

    async def prepare_submission(  # pragma: no cover - env-gated
        self, ctx: PrepareContext
    ) -> PrepareResult:
        """Dry-run fill against BOSS Web.

        .. note::

           The full Playwright navigation/fill/classify logic is implemented in
           a follow-up task once the safety contracts are audited. Until then,
           the env-gated real adapter returns ``unknown`` instead of
           synthesizing a successful filled preview.
        """
        _log.info(
            "boss.adapter.prepare",
            application_id=ctx.application_id,
            target_resource=ctx.target_resource,
        )
        return PrepareResult(
            outcome=PrepareOutcome.unknown,
            failure_code=prepare_failure_code(PrepareOutcome.unknown),
            message="real BOSS adapter is not implemented yet",
        )

    async def submit_prepared(  # pragma: no cover - env-gated
        self, ctx: SubmitContext
    ) -> SubmitResult:
        """Final submit against BOSS Web.

        .. note::

           The full Playwright final-submit logic is implemented in a follow-up
           task. Until then, the env-gated real adapter returns ``unknown`` so
           the product never marks an application submitted without observing a
           real platform result.
        """
        _log.info(
            "boss.adapter.submit",
            application_id=ctx.application_id,
            target_resource=ctx.target_resource,
        )
        return SubmitResult(
            outcome=SubmitOutcome.unknown,
            failure_code="platform_unknown_result",
            message="real BOSS adapter is not implemented yet",
            occurred_at=datetime.now(UTC),
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

    # The mapping helpers are imported to keep the real adapter honest about
    # using the shared failure-code table once the full logic lands.
    _prepare_failure_code: Any = staticmethod(prepare_failure_code)
