"""BOSS immediate-communicate service — prepare + execute-gate workflow.

This module owns the *prepare* and *execute-gate* phases of the BOSS
"立即沟通" (immediate communicate) pilot:

- **Prepare** (synchronous, no browser) — reads a previously persisted
  ``boss_match_decision`` :class:`GeneratedArtifact` whose decision is
  ``communicate``, extracts the ``opening_message``, re-validates it, and drafts
  a ``boss_immediate_communicate`` :class:`ApplicationAction` in
  ``approval_required`` status. The action is bound to a ``payload_hash`` (over
  the exact opening message + target) and an external idempotency key (reusing
  the proven ``{application_id}:{action_type}:{payload_hash}`` shape).
- **Execute** (synchronous, guards only in this subtask) — runs the full
  approval + idempotency guard chain mirroring
  :func:`run_platform_guided_submit_submit`, records ``external_started_at``,
  and appends a ``boss_communicate_started`` timeline event. The actual browser
  adapter call is inserted in a later subtask; for now the execute returns a
  "guards passed" result so the approval/idempotency contract is fully testable.

Safety invariants:

- No raw JD, raw resume, cookies, tokens, or page HTML are persisted. The action
  preview carries only IDs, the opening message (which is the exact payload the
  user approves), and the resume version reference.
- Cross-user access returns 404 (not 403), matching the convention used by
  ``approval_action_service`` and ``platform_submission_service``.
- One ``application_id`` per call — no batch paths.
- An unapproved execute raises :class:`BossCommunicateBlockedError` (mapped to
  HTTP 409 by the API layer).
- A replay against a terminal idempotency key returns the existing action
  without touching the browser (design.md §H1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.opening_message_guard import validate_opening_message
from app.core.logging import get_logger
from app.db.models.models import (
    ApplicationAction,
    ApplicationRecord,
    JobPosting,
    ResumeVersion,
    UserProfile,
)
from app.db.repositories import (
    application_action_repo,
    application_repo,
    generated_artifact_repo,
)
from app.db.repositories.application_action_repo import CLEAR
from app.platforms.base import (
    COMMUNICATION_FAILURE_CODES,
    CommunicationExecuteContext,
    CommunicationExecuteResult,
    CommunicationOutcome,
)
from app.platforms.boss.registry import get_adapter
from app.schemas.application import (
    ApplicationFailureCategory,
    ApplicationFailureNextAction,
)
from app.schemas.application_action import (
    ApplicationActionOut,
    ApplicationActionPreview,
    ApplicationActionSourceSnapshot,
    ApprovalBlockedError,
    ExternalActionResult,
    ExternalActionResultStatus,
    ExternalActionStatus,
    ExternalActionType,
)
from app.schemas.boss_match_decision import MatchDecision, MatchDecisionModelOutput
from app.services.application_state import (
    build_failure_envelope,
    build_source_snapshot,
)
from app.services.approval_boundary import (
    assert_action_approved,
    compute_external_idempotency_key,
    compute_payload_hash,
)

_log = get_logger("app.services.boss_communicate_service")

#: The artifact type produced by the match-decision workflow. The prepare flow
#: reads the most recent artifact of this type for the given job + user.
_MATCH_ARTIFACT_TYPE = "boss_match_decision"

#: Prompt versions bound into the source snapshot. The communicate prepare flow
#: does not call a model, so only the readiness prompt version is recorded for
#: staleness parity with readiness artifacts (mirrors platform_submission_service).
_PROMPT_VERSIONS: dict[str, str] = {"readiness": "pilot"}


class BossCommunicateBlockedError(Exception):
    """Raised when a communicate execute is blocked by the approval/idempotency guard.

    ``reason`` is a stable machine code (mirrors
    :class:`ApprovalBlockedError.reason``); ``message`` is a safe user-facing
    string. The API layer maps this to HTTP 409. ``action_id`` lets the caller
    re-fetch the action after the guard refused execution.
    """

    def __init__(self, *, reason: str, message: str, action_id: str) -> None:
        self.reason = reason
        self.message = message
        self.action_id = action_id
        super().__init__(reason, message, action_id)


# ---------------------------------------------------------------------------
# Prepare
# ---------------------------------------------------------------------------


def prepare_communicate_action(
    db: Session,
    current_user: UserProfile,
    job_id: str,
    *,
    resume_version_id: str,
    match_artifact_id: str,
) -> ApplicationAction:
    """Draft a ``boss_immediate_communicate`` action from a match-decision artifact.

    Reads the ``boss_match_decision`` artifact, asserts the decision is
    ``communicate`` with a non-null ``opening_message``, re-validates the
    opening message, and creates/refreshes a ``boss_immediate_communicate``
    :class:`ApplicationAction` in ``approval_required`` status.

    Raises ``HTTPException`` (404/422) on ownership, missing-data, or
    decision-state failures.
    """
    # --- Load + verify ownership of the job. ---
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        _log.info(
            "boss_communicate.job_not_found",
            user_id=current_user.id,
            job_id=job_id,
        )
        raise HTTPException(status_code=404, detail="job not found")

    # --- Load + verify ownership of the resume version. ---
    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        _log.info(
            "boss_communicate.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    # Verify the resume version belongs to the current user (via parent resume).
    from app.db.repositories import resume_repo

    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        _log.info(
            "boss_communicate.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
            resume_id=version.resume_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    # --- Load + verify the match-decision artifact. ---
    artifact = generated_artifact_repo.get(db, match_artifact_id)
    if artifact is None or artifact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="match artifact not found")

    if artifact.artifact_type != _MATCH_ARTIFACT_TYPE:
        _log.info(
            "boss_communicate.artifact_wrong_type",
            user_id=current_user.id,
            artifact_id=match_artifact_id,
            artifact_type=artifact.artifact_type,
        )
        raise HTTPException(status_code=404, detail="match artifact not found")

    # The artifact must be for this job.
    if artifact.job_id != job_id:
        _log.info(
            "boss_communicate.artifact_job_mismatch",
            user_id=current_user.id,
            artifact_id=match_artifact_id,
            artifact_job_id=artifact.job_id,
            job_id=job_id,
        )
        raise HTTPException(status_code=404, detail="match artifact not found")

    # --- Parse the artifact content into the validated model output. ---
    try:
        match_output = MatchDecisionModelOutput.model_validate_json(artifact.content)
    except Exception as exc:  # pragma: no cover — defensive; artifact is always valid JSON
        _log.warning(
            "boss_communicate.artifact_parse_failed",
            user_id=current_user.id,
            artifact_id=match_artifact_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=422,
            detail="match artifact content is invalid",
        ) from exc

    # --- Assert the decision is communicate. ---
    if match_output.decision is not MatchDecision.communicate:
        raise HTTPException(
            status_code=422,
            detail=(
                "match decision must be 'communicate' to prepare a communicate "
                f"action (got '{match_output.decision.value}')"
            ),
        )

    # --- Assert the opening message is present. ---
    if match_output.opening_message is None:
        raise HTTPException(
            status_code=422,
            detail="match decision has no opening message",
        )

    # --- Re-validate the opening message (defensive — safety gate should have
    # cleared invalid messages, but we double-check before binding the payload). ---
    _, msg_error = validate_opening_message(match_output.opening_message)
    if msg_error is not None:
        _log.warning(
            "boss_communicate.opening_message_invalid",
            user_id=current_user.id,
            artifact_id=match_artifact_id,
            error=msg_error,
        )
        raise HTTPException(
            status_code=422,
            detail=f"opening message is no longer valid: {msg_error}",
        )

    # --- Find or create the application record. ---
    record = application_repo.find_duplicate(
        db,
        user_id=current_user.id,
        job_id=job.id,
        resume_version_id=resume_version_id,
    )
    if record is None:
        event = application_repo.build_event(
            type="created",
            actor="agent",
            to_status="planned",
            summary="BOSS 立即沟通投递记录已创建",
            metadata={
                "job_id": job.id,
                "resume_version_id": resume_version_id,
                "source": "boss_communicate_prepare",
            },
        )
        record = application_repo.create_for_user(
            db,
            user_id=current_user.id,
            job_id=job.id,
            resume_version_id=resume_version_id,
            status="planned",
            timeline=[event],
        )

    # --- Build the action preview + payload hash + idempotency key. ---
    preview = ApplicationActionPreview(
        action_type=ExternalActionType.boss_immediate_communicate,
        target_platform="boss",
        target_resource=job.id,
        selected_artifact_ids=[match_artifact_id],
        outgoing_text=match_output.opening_message,
        resume_file_reference=resume_version_id,
    )
    payload_hash = compute_payload_hash(preview)
    idempotency_key = compute_external_idempotency_key(
        application_id=record.id,
        action_type=ExternalActionType.boss_immediate_communicate,
        payload_hash=payload_hash,
    )

    # --- Compute the source hash (readiness-parity with platform_submit). ---
    source_snapshot = build_source_snapshot(
        job_id=job.id,
        job_updated_at=job.updated_at,
        resume_version_id=version.id,
        resume_version_no=version.version_no,
        profile_updated_at=current_user.updated_at,
        prompt_versions=_PROMPT_VERSIONS,
    )
    source_hash = source_snapshot.source_hash

    # --- Build the source_snapshot dict. Extra keys (job_url_hash,
    # decision_trace) are stored alongside the fixed ApplicationActionSourceSnapshot
    # fields in the JSON column. ---
    decision_trace: dict[str, Any] = match_output.model_dump(
        mode="json", exclude={"opening_message"}
    )
    source_snapshot_dict: dict[str, Any] = ApplicationActionSourceSnapshot(
        job_id=job.id,
        resume_version_id=version.id,
        artifact_ids=[match_artifact_id],
        source_hash=source_hash,
    ).model_dump()
    source_snapshot_dict["job_url_hash"] = job.external_id
    source_snapshot_dict["decision_trace"] = decision_trace

    # --- Reuse an existing non-terminal boss_immediate_communicate action, or
    # create a new one (mirrors _persist_filled_preview). ---
    existing = _find_active_communicate_action(db, record.id, record.user_id)
    if existing is not None:
        application_action_repo.update(
            db,
            existing,
            status=ExternalActionStatus.approval_required.value,
            payload_preview=preview.model_dump(mode="json"),
            payload_hash=payload_hash,
            source_snapshot=source_snapshot_dict,
            external_idempotency_key=idempotency_key,
            stale_reason=CLEAR,
            approval=CLEAR,
        )
        action = existing
    else:
        action = application_action_repo.create(
            db,
            application_id=record.id,
            user_id=record.user_id,
            action_type=ExternalActionType.boss_immediate_communicate.value,
            status=ExternalActionStatus.approval_required.value,
            payload_preview=preview.model_dump(mode="json"),
            payload_hash=payload_hash,
            source_snapshot=source_snapshot_dict,
            external_idempotency_key=idempotency_key,
        )

    # --- Append a timeline event (no application status change — the approval
    # boundary is informational at this stage). ---
    event = application_repo.build_event(
        type="boss_communicate_previewed",
        actor="system",
        to_status=record.status,
        summary="BOSS 立即沟通动作已草拟，等待审批",
        metadata={
            "action_id": action.id,
            "payload_hash": payload_hash,
            "match_artifact_id": match_artifact_id,
            "target_platform": "boss",
        },
    )
    application_repo.append_timeline_event(db, record, event=event)

    db.commit()
    db.refresh(action)

    _log.info(
        "boss_communicate.prepared",
        user_id=current_user.id,
        application_id=record.id,
        action_id=action.id,
        payload_hash=payload_hash,
    )
    return action


def _find_active_communicate_action(
    db: Session, application_id: str, user_id: str
) -> ApplicationAction | None:
    """Return the most recent non-terminal ``boss_immediate_communicate`` action, or None.

    "Non-terminal" means the action has not yet produced an external result
    (``external_result_status`` is NULL). Repeated prepares refresh this action
    in place instead of creating duplicates (mirrors
    ``_find_active_platform_submit_action``).
    """
    return (
        db.execute(
            select(ApplicationAction).where(
                (ApplicationAction.application_id == application_id)
                & (ApplicationAction.user_id == user_id)
                & (
                    ApplicationAction.action_type
                    == ExternalActionType.boss_immediate_communicate.value
                )
                & (ApplicationAction.external_result_status.is_(None))
            )
            .order_by(ApplicationAction.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


# ---------------------------------------------------------------------------
# Execute — context loading + guard chain
# ---------------------------------------------------------------------------


def load_communicate_execute_context(
    db: Session,
    current_user: UserProfile,
    *,
    application_id: str,
    action_id: str,
    job_id: str,
) -> tuple[ApplicationRecord, ApplicationAction]:
    """Load + verify ownership for a communicate execute request.

    Raises ``HTTPException`` (404) on ownership or action-type failures.
    Returns ``(record, action)`` so the execute service can run the guard chain.
    """
    record = application_repo.get_for_user(db, application_id, current_user.id)
    if record is None:
        _log.info(
            "boss_communicate.application_not_found",
            user_id=current_user.id,
            application_id=application_id,
        )
        raise HTTPException(status_code=404, detail="application not found")

    action = application_action_repo.get_for_user_and_application(
        db,
        action_id=action_id,
        application_id=application_id,
        user_id=current_user.id,
    )
    if action is None:
        _log.info(
            "boss_communicate.action_not_found",
            user_id=current_user.id,
            application_id=application_id,
            action_id=action_id,
        )
        raise HTTPException(status_code=404, detail="action not found")

    # The action must be a boss_immediate_communicate action.
    if action.action_type != ExternalActionType.boss_immediate_communicate.value:
        raise HTTPException(status_code=404, detail="action not found")

    # The action's target_resource must match the job_id in the path.
    preview = ApplicationActionPreview.model_validate(action.payload_preview)
    if preview.target_resource != job_id:
        raise HTTPException(status_code=404, detail="action not found")

    return record, action


def _compute_current_source_hash(
    db: Session,
    *,
    record: ApplicationRecord,
    current_user: UserProfile,
    action: ApplicationAction,
) -> str:
    """Recompute the current source hash from the current job/resume/profile.

    Mirrors ``run_platform_guided_submit_submit``: if any source row is missing
    or cross-user, a :class:`BossCommunicateBlockedError` is raised with
    ``reason="source_unavailable"``.
    """
    job = db.get(JobPosting, record.job_id)
    if job is None or job.user_id != current_user.id:
        raise BossCommunicateBlockedError(
            reason="source_unavailable",
            message="job context is unavailable",
            action_id=action.id,
        )

    resume_version_id = record.resume_version_id
    if resume_version_id is None:
        raise BossCommunicateBlockedError(
            reason="source_unavailable",
            message="resume version is missing",
            action_id=action.id,
        )

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        raise BossCommunicateBlockedError(
            reason="source_unavailable",
            message="resume version is unavailable",
            action_id=action.id,
        )

    profile = db.get(UserProfile, current_user.id)
    if profile is None:  # pragma: no cover — current_user is always valid
        raise BossCommunicateBlockedError(
            reason="source_unavailable",
            message="user profile is unavailable",
            action_id=action.id,
        )

    snapshot = build_source_snapshot(
        job_id=job.id,
        job_updated_at=job.updated_at,
        resume_version_id=version.id,
        resume_version_no=version.version_no,
        profile_updated_at=profile.updated_at,
        prompt_versions=_PROMPT_VERSIONS,
    )
    return snapshot.source_hash


def _append_communicate_started_timeline(
    db: Session,
    record: ApplicationRecord,
    *,
    action: ApplicationAction,
    idempotency_key: str | None,
) -> None:
    """Append a ``boss_communicate_started`` timeline event (no status change)."""
    event = application_repo.build_event(
        type="boss_communicate_started",
        actor="system",
        to_status=record.status,
        summary="BOSS 立即沟通执行已开始",
        metadata={
            "action_id": action.id,
            "external_idempotency_key": idempotency_key,
        },
    )
    application_repo.append_timeline_event(db, record, event=event)


def _append_communicate_blocked_timeline(
    db: Session,
    record: ApplicationRecord,
    *,
    action: ApplicationAction,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Append a ``boss_communicate_blocked`` timeline event (no status change)."""
    event = application_repo.build_event(
        type="boss_communicate_blocked",
        actor="system",
        to_status=record.status,
        summary=f"BOSS 立即沟通执行被阻止: {reason}",
        metadata={
            "action_id": action.id,
            "reason": reason,
            "external_idempotency_key": idempotency_key,
        },
    )
    application_repo.append_timeline_event(db, record, event=event)


async def run_boss_communicate_execute(
    db: Session,
    *,
    current_user: UserProfile,
    job_id: str,
    application_id: str,
    action_id: str,
) -> tuple[ApplicationRecord, ApplicationAction, bool]:
    """Run the approval + idempotency guards for a communicate execute.

    Mirrors ``run_platform_guided_submit_submit``:

    1. Load + verify ownership (``load_communicate_execute_context``).
    2. Guard 0: idempotency key — if a terminal result already exists, return
       the existing action without touching the browser (design.md §H1).
    3. Recompute ``current_payload_hash`` from the stored preview.
    4. Recompute ``current_source_hash`` from the current job/resume/profile.
    5. Guard 1: ``assert_action_approved`` → catch ``ApprovalBlockedError`` →
       re-raise as :class:`BossCommunicateBlockedError` (mapped to 409).
    6. Record ``external_started_at`` + flush.
    7. Append ``boss_communicate_started`` timeline event.
    8. **Invoke the browser adapter** — ``adapter.execute_communication()`` to
       click "立即沟通", fill the opening message, send it, and read the result.
    9. **Persist the terminal result** — map the adapter outcome to
       ``external_result_status`` and append the matching timeline event.

    Returns ``(record, action, replayed)`` after the adapter call completes.
    ``replayed`` is ``True`` when Guard 0 short-circuited (terminal result
    already exists) so the API can surface an idempotency-replay message.
    Raises :class:`BossCommunicateBlockedError` when the approval guard blocks
    execution (the caller maps it to 409).
    """
    record, action = load_communicate_execute_context(
        db,
        current_user,
        application_id=application_id,
        action_id=action_id,
        job_id=job_id,
    )

    # --- Guard 0: external idempotency key. ---
    idempotency_key = action.external_idempotency_key
    if idempotency_key is not None:
        terminal = application_action_repo.get_terminal_for_idempotency_key(
            db,
            external_idempotency_key=idempotency_key,
            user_id=current_user.id,
        )
        if terminal is not None:
            # Already submitted/duplicate/unknown/failed — return the existing
            # action. Append a timeline event noting the idempotent replay.
            _append_communicate_blocked_timeline(
                db,
                record,
                action=action,
                reason="idempotency_replay",
                idempotency_key=idempotency_key,
            )
            db.commit()
            db.refresh(action)
            return record, action, True

    # --- Recompute the current payload hash from the stored preview. ---
    preview = ApplicationActionPreview.model_validate(action.payload_preview)
    current_payload_hash = compute_payload_hash(preview)

    # --- Recompute the current source hash from the current job/resume/profile. ---
    current_source_hash = _compute_current_source_hash(
        db, record=record, current_user=current_user, action=action
    )

    # --- Guard 1: approval boundary. ---
    try:
        assert_action_approved(
            ApplicationActionOut.model_validate(action),
            current_payload_hash=current_payload_hash,
            current_source_hash=current_source_hash,
        )
    except ApprovalBlockedError as exc:
        raise BossCommunicateBlockedError(
            reason=exc.reason,
            message=exc.message,
            action_id=action.id,
        ) from exc

    # --- Record the external-start timestamp so a concurrent execute is
    # auditable (defensive — the request is synchronous). ---
    started_at = datetime.now(UTC)
    application_action_repo.update(
        db,
        action,
        external_started_at=started_at,
    )
    db.flush()

    # --- Append the timeline event marking the execute start. ---
    _append_communicate_started_timeline(
        db,
        record,
        action=action,
        idempotency_key=idempotency_key,
    )

    # --- Step 8: Invoke the browser adapter. ---
    # The adapter is selected via get_adapter(): fake by default (tests/dev),
    # userscript bridge when boss_userscript_bridge_enabled is set, real
    # Playwright/CDP when boss_adapter_enabled is set.
    preview_for_ctx = ApplicationActionPreview.model_validate(action.payload_preview)
    source_snapshot_dict = action.source_snapshot or {}
    job_url_hash = source_snapshot_dict.get("job_url_hash") or preview_for_ctx.target_resource

    adapter = get_adapter()
    result = await adapter.execute_communication(
        CommunicationExecuteContext(
            application_id=record.id,
            target_platform=preview_for_ctx.target_platform,
            target_resource=job_url_hash,
            opening_message=preview_for_ctx.outgoing_text or "",
            source_hash=current_source_hash,
        )
    )

    # --- Step 9: Persist the terminal result. ---
    _persist_communicate_result(
        db,
        record=record,
        action=action,
        result=result,
        started_at=started_at,
    )

    db.commit()
    db.refresh(action)

    _log.info(
        "boss_communicate.completed",
        user_id=current_user.id,
        application_id=record.id,
        action_id=action.id,
        outcome=result.outcome.value,
        idempotency_key=idempotency_key,
    )
    return record, action, False


# ---------------------------------------------------------------------------
# Persist the terminal communicate result
# ---------------------------------------------------------------------------


def _persist_communicate_result(
    db: Session,
    *,
    record: ApplicationRecord,
    action: ApplicationAction,
    result: CommunicationExecuteResult,
    started_at: datetime,
) -> None:
    """Persist the terminal result of an immediate-communicate execute.

    Maps the adapter outcome to:

    - ``succeeded`` → ``external_result_status=submitted`` (the durable enum
      uses ``submitted`` for confirmed success across all action types),
      ``boss_communicate_succeeded`` timeline event. Application status is
      unchanged — communicate is a side-channel message, not a formal submit.
    - ``duplicate`` → ``external_result_status=duplicate``,
      ``boss_communicate_duplicate`` timeline event + failure envelope.
    - ``failed`` → ``external_result_status=failed``,
      ``boss_communicate_failed`` timeline event + failure envelope.
    - ``unknown`` → ``external_result_status=unknown``,
      ``boss_communicate_unknown`` timeline event + failure envelope. Hard
      stop — no automatic retry.

    All persisted metadata is sanitized: only IDs, outcome, and the failure
    code. No cookies, tokens, credentials, raw JD, raw resume, or page HTML.
    """
    completed_at = datetime.now(UTC)
    outcome = result.outcome

    if outcome is CommunicationOutcome.succeeded:
        external_result = ExternalActionResult(
            result_status=ExternalActionResultStatus.submitted,
            started_at=started_at,
            completed_at=completed_at,
            result={
                "outcome": "succeeded",
                "platform_reference": result.platform_reference,
                "target_platform": action.payload_preview.get("target_platform"),
            },
        )
        application_action_repo.update(
            db,
            action,
            external_completed_at=completed_at,
            external_result_status=ExternalActionResultStatus.submitted.value,
            external_result=external_result.model_dump(mode="json"),
        )
        event = application_repo.build_event(
            type="boss_communicate_succeeded",
            actor="system",
            to_status=record.status,
            summary="BOSS 立即沟通已成功发送",
            metadata={
                "action_id": action.id,
                "platform_reference": result.platform_reference,
            },
        )
        application_repo.append_timeline_event(db, record, event=event)
        return

    # Non-succeeded outcome: map to the failure envelope code + next action.
    code, next_action_str = COMMUNICATION_FAILURE_CODES[outcome]
    next_action = ApplicationFailureNextAction(next_action_str)

    result_status = {
        CommunicationOutcome.duplicate: ExternalActionResultStatus.duplicate,
        CommunicationOutcome.failed: ExternalActionResultStatus.failed,
        CommunicationOutcome.unknown: ExternalActionResultStatus.unknown,
    }[outcome]

    external_result = ExternalActionResult(
        result_status=result_status,
        started_at=started_at,
        completed_at=completed_at,
        result={
            "failure_code": code,
            "outcome": outcome.value,
            "message": result.message,
        },
    )
    application_action_repo.update(
        db,
        action,
        external_completed_at=completed_at,
        external_result_status=result_status.value,
        external_result=external_result.model_dump(mode="json"),
    )

    envelope = build_failure_envelope(
        category=ApplicationFailureCategory.platform,
        code=code,
        message=result.message or f"boss communicate failed: {outcome.value}",
        retryable=False,  # communicate never auto-retries
        next_action=next_action,
        agent_run_id=None,
        source_ids={
            "application_id": record.id,
            "action_id": action.id,
            "target_platform": action.payload_preview.get("target_platform"),
        },
        occurred_at=completed_at,
    )

    event_type = {
        CommunicationOutcome.duplicate: "boss_communicate_duplicate",
        CommunicationOutcome.failed: "boss_communicate_failed",
        CommunicationOutcome.unknown: "boss_communicate_unknown",
    }[outcome]

    event = application_repo.build_event(
        type=event_type,
        actor="system",
        to_status=record.status,
        summary=f"BOSS 立即沟通: {code}",
        metadata={
            "action_id": action.id,
            "error_code": code,
            "outcome": outcome.value,
            "diagnostic_reference": result.diagnostic_reference,
        },
    )
    application_repo.update_status_with_event(
        db,
        record,
        new_status=record.status,
        event=event,
        latest_error=envelope.model_dump(mode="json"),
    )


__all__ = [
    "BossCommunicateBlockedError",
    "load_communicate_execute_context",
    "prepare_communicate_action",
    "run_boss_communicate_execute",
]
