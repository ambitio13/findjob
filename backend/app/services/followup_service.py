"""Follow-up suggestion + threshold calibration service (Phase 5).

Closes the advice loop over Phase 1 outcome data:

1. **Suggestion scan** — deterministic rules over the user's applications and
   outcome events:

   - *Rule A* (stale submission): ``submitted`` with no reply-implying
     outcome for ``NO_REPLY_DAYS`` days → suggest changing the opening
     message and re-engaging.
   - *Rule B* (capitalize on replies): applications that got a reply but have
     no pending skill-gap plan → suggest building one (detail embeds the
     latest JD analysis ``skill_gaps`` when available).
   - *Rule C* (failure patterns, via :class:`OutcomeReflector`): job
     directions whose reply rate stays low across enough submissions → one
     suggestion per *active* application in that direction.

2. **Threshold calibration** — purely statistical: bucket submitted
   applications with outcome evidence by the job's latest match score, take
   the 25th percentile of the replied group's scores, clamp to a safe band,
   and persist as the new ``COMMUNICATE_MIN_SCORE``. No model training.

Safety invariants:

- Suggestions are advisory rows only. Acting on one re-enters the existing
  generation + approval boundary; nothing here triggers external effects.
- Dedup: a user never gets a second *pending* suggestion of the same type
  for the same application.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.opening_message_guard import COMMUNICATE_MIN_SCORE
from app.agents.reflector import OutcomeReflector, OutcomeSample
from app.core.logging import get_logger
from app.db.models.models import (
    ApplicationRecord,
    FollowUpSuggestion,
    JobPosting,
    UserProfile,
)
from app.db.repositories import (
    application_outcome_repo,
    application_repo,
    follow_up_suggestion_repo,
    generated_artifact_repo,
    threshold_calibration_repo,
    user_profile_repo,
)
from app.schemas.application import ApplicationStatus
from app.schemas.followup import SuggestionStatus, SuggestionType
from app.schemas.outcome import OutcomeType

_log = get_logger("app.services.followup_service")

#: Days without a reply before a submitted application gets a nudge.
NO_REPLY_DAYS = 3

#: Minimum applications with outcome evidence before any calibration runs.
CALIBRATION_MIN_SAMPLES = 5

#: Quantile of the replied group's match scores used as the raw threshold.
CALIBRATION_QUANTILE = 0.25

#: Hard band for the calibrated threshold (never loosen or tighten past this).
CALIBRATION_MIN_THRESHOLD = 0.4
CALIBRATION_MAX_THRESHOLD = 0.8

#: Outcomes implying HR replied (mirrors metrics_service semantics).
_REPLY_OUTCOMES: frozenset[OutcomeType] = frozenset(
    {OutcomeType.replied, OutcomeType.interview, OutcomeType.offer}
)

#: Statuses proving the application reached the platform (calibration input).
_SUBMITTED_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {ApplicationStatus.submitted, ApplicationStatus.interviewing, ApplicationStatus.rejected}
)

#: Statuses still eligible for direction-level advice (not concluded yet).
_ACTIVE_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.planned,
        ApplicationStatus.preparing,
        ApplicationStatus.materials_ready,
        ApplicationStatus.approval_required,
        ApplicationStatus.approved,
        ApplicationStatus.paused,
    }
)


def _replied_application_ids(outcomes: list) -> set[str]:
    """Application ids with at least one reply-implying outcome."""
    return {
        outcome.application_id
        for outcome in outcomes
        if OutcomeType(outcome.outcome_type) in _REPLY_OUTCOMES
    }


def _submitted_at(record: ApplicationRecord) -> datetime:
    """When the record entered ``submitted``.

    Scans the timeline newest-first for the status change into ``submitted``;
    falls back to ``created_at`` when the timeline lacks the event (records
    created before timeline tracking). Naive timestamps are treated as UTC.
    """
    for event in reversed(record.timeline or []):
        if event.get("to_status") != ApplicationStatus.submitted.value:
            continue
        raw = event.get("at")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return record.created_at


def _latest_skill_gaps(db: Session, job_id: str) -> list[str]:
    """Skill gaps from the job's newest ``jd_analysis`` artifact (if any)."""
    rows, _total = generated_artifact_repo.list_for_job(
        db, job_id, artifact_type="jd_analysis", page=1, page_size=1
    )
    if not rows:
        return []
    try:
        structured = json.loads(rows[0].content)
    except (TypeError, ValueError):
        return []
    gaps = structured.get("skill_gaps")
    return [str(g) for g in gaps] if isinstance(gaps, list) else []


def _create_if_new(
    db: Session,
    *,
    user_id: str,
    record: ApplicationRecord,
    suggestion_type: SuggestionType,
    title: str,
    detail: str | None,
) -> FollowUpSuggestion | None:
    """Insert the suggestion unless an identical pending one exists."""
    if follow_up_suggestion_repo.has_pending(
        db,
        user_id=user_id,
        application_id=record.id,
        suggestion_type=suggestion_type.value,
    ):
        return None
    return follow_up_suggestion_repo.create(
        db,
        user_id=user_id,
        application_id=record.id,
        suggestion_type=suggestion_type.value,
        title=title,
        detail=detail,
    )


def generate_suggestions_for_user(
    db: Session, user_id: str, *, now: datetime | None = None
) -> list[FollowUpSuggestion]:
    """Run rules A/B/C for one user; return the created rows.

    Flushes but does NOT commit — callers own the transaction so the
    suggestions and any calibration row land atomically.
    """
    now = now or datetime.now(UTC)
    records = application_repo.list_all_for_user(db, user_id)
    if not records:
        return []
    outcomes = application_outcome_repo.list_for_user(db, user_id)
    replied_ids = _replied_application_ids(outcomes)

    job_ids = sorted({r.job_id for r in records})
    jobs: dict[str, JobPosting] = {
        job.id: job
        for job in db.execute(select(JobPosting).where(JobPosting.id.in_(job_ids)))
        .scalars()
        .all()
    }
    created: list[FollowUpSuggestion] = []

    for record in records:
        status = ApplicationStatus(record.status)

        # Rule A — submitted, silent for NO_REPLY_DAYS, never replied.
        if status is ApplicationStatus.submitted and record.id not in replied_ids:
            silent_for = now - _submitted_at(record)
            if silent_for >= timedelta(days=NO_REPLY_DAYS):
                row = _create_if_new(
                    db,
                    user_id=user_id,
                    record=record,
                    suggestion_type=SuggestionType.change_opening_message,
                    title="已投递 3 天未获回复，建议更换开场白重新触达",
                    detail=(
                        "该投递已超过 "
                        f"{NO_REPLY_DAYS} 天没有任何回复。建议生成一版新的开场白"
                        "（换角度切入岗位需求），由你确认后发送。"
                    ),
                )
                if row is not None:
                    created.append(row)

        # Rule B — HR replied; make sure a skill-gap plan exists to follow up.
        if record.id in replied_ids:
            gaps = _latest_skill_gaps(db, record.job_id)
            detail = (
                "HR 已回复，建议整理技能补齐清单为后续沟通/面试做准备。"
                + (f"最新 JD 分析识别的差距：{'、'.join(gaps)}。" if gaps else "")
            )
            row = _create_if_new(
                db,
                user_id=user_id,
                record=record,
                suggestion_type=SuggestionType.skill_gap_plan,
                title="HR 已回复：整理技能补齐清单",
                detail=detail,
            )
            if row is not None:
                created.append(row)

    # Rule C — outcome reflector: flag directions with persistently low reply
    # rates, then nudge every still-active application in those directions.
    samples = [
        OutcomeSample(
            direction=(jobs.get(r.job_id).direction if jobs.get(r.job_id) else "") or "",
            replied=r.id in replied_ids,
        )
        for r in records
        if ApplicationStatus(r.status) in _SUBMITTED_STATUSES
    ]
    finding = OutcomeReflector().reflect(samples)
    for stat in finding.low_reply_directions:
        for record in records:
            if ApplicationStatus(record.status) not in _ACTIVE_STATUSES:
                continue
            job = jobs.get(record.job_id)
            direction = (job.direction if job else "") or ""
            normalized = direction.strip() or "unknown"
            if normalized != stat.direction:
                continue
            row = _create_if_new(
                db,
                user_id=user_id,
                record=record,
                suggestion_type=SuggestionType.low_reply_rate_direction,
                title=f"「{stat.direction}」方向回复率偏低，建议调整策略",
                detail=(
                    f"该方向最近 {stat.submitted} 次投递仅 {stat.replied} 次获得回复"
                    f"（{stat.reply_rate:.0%}）。建议重新审视该方向的岗位匹配度、"
                    "开场白策略，或先补齐关键技能再投递。"
                ),
            )
            if row is not None:
                created.append(row)

    if created:
        _log.info("followup.suggestions_created", user_id=user_id, created=len(created))
    return created


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0, 1]) of a sorted list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def calibrate_match_threshold(db: Session, user_id: str) -> float | None:
    """Derive and persist the communicate-gate threshold from outcome data.

    Samples = submitted applications that have both outcome evidence and a
    latest match score. The raw threshold is the 25th percentile of the
    replied group's scores (so at least ~75% of past replies came from above
    it), clamped to ``[0.4, 0.8]``. Returns the new threshold, or ``None``
    when evidence is insufficient (default stays in force).

    Flushes but does NOT commit — the caller owns the transaction.
    """
    records = application_repo.list_all_for_user(db, user_id)
    outcomes = application_outcome_repo.list_for_user(db, user_id)
    if not records or not outcomes:
        return None

    apps_with_outcomes = {o.application_id for o in outcomes}
    replied_ids = _replied_application_ids(outcomes)
    job_ids = sorted({r.job_id for r in records if r.id in apps_with_outcomes})
    scores = application_outcome_repo.latest_match_scores_by_job(db, job_ids)

    sample_scores: list[float] = []
    replied_scores: list[float] = []
    for record in records:
        if record.id not in apps_with_outcomes:
            continue
        score = scores.get(record.job_id)
        if score is None:
            continue
        sample_scores.append(score)
        if record.id in replied_ids:
            replied_scores.append(score)

    if len(sample_scores) < CALIBRATION_MIN_SAMPLES or not replied_scores:
        return None

    raw_p25 = _percentile(sorted(replied_scores), CALIBRATION_QUANTILE)
    threshold = round(
        min(max(raw_p25 / 100.0, CALIBRATION_MIN_THRESHOLD), CALIBRATION_MAX_THRESHOLD), 4
    )
    threshold_calibration_repo.create(
        db,
        user_id=user_id,
        threshold=threshold,
        method="quantile_p25",
        sample_count=len(sample_scores),
        details={
            "total_samples": len(sample_scores),
            "replied_samples": len(replied_scores),
            "raw_p25_score": round(raw_p25, 4),
            "clamp": [CALIBRATION_MIN_THRESHOLD, CALIBRATION_MAX_THRESHOLD],
        },
    )
    _log.info(
        "followup.threshold_calibrated",
        user_id=user_id,
        threshold=threshold,
        samples=len(sample_scores),
    )
    return threshold


def active_match_threshold(db: Session, user_id: str) -> float:
    """The communicate-gate threshold in force for ``user_id``.

    Newest calibration wins; the module default applies until the first
    successful calibration.
    """
    latest = threshold_calibration_repo.latest_for_user(db, user_id)
    return latest.threshold if latest else COMMUNICATE_MIN_SCORE


def resolve_suggestion(
    db: Session,
    current_user: UserProfile,
    suggestion_id: str,
    *,
    target: SuggestionStatus,
) -> FollowUpSuggestion:
    """Mark an owned pending suggestion ``actioned`` or ``dismissed``.

    Cross-user or unknown ids return 404 (codebase convention). Already
    resolved rows return 409 so a double-click cannot re-resolve. Resolution
    only touches the suggestion row itself — any external action still has to
    go through the existing generation + approval flow.
    """
    if target not in (SuggestionStatus.actioned, SuggestionStatus.dismissed):
        raise ValueError(f"invalid resolution target: {target}")
    row = follow_up_suggestion_repo.get_for_user(db, suggestion_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if row.status != SuggestionStatus.pending.value:
        raise HTTPException(status_code=409, detail="suggestion already resolved")
    follow_up_suggestion_repo.mark_resolved(
        db, row, status=target.value, resolved_at=datetime.now(UTC)
    )
    db.commit()
    db.refresh(row)
    _log.info(
        "followup.suggestion_resolved",
        user_id=current_user.id,
        suggestion_id=suggestion_id,
        status=target.value,
    )
    return row


def run_follow_up_scan(
    db: Session, current_user: UserProfile, *, now: datetime | None = None
):
    """Scan + calibrate for one user in one transaction.

    Returns ``(created_rows, active_threshold, calibrated_now)``.
    """
    created = generate_suggestions_for_user(db, current_user.id, now=now)
    calibrated = calibrate_match_threshold(db, current_user.id)
    db.commit()
    threshold = calibrated if calibrated is not None else active_match_threshold(
        db, current_user.id
    )
    return created, threshold, calibrated is not None


def scan_all_users(db: Session) -> int:
    """Scheduler entrypoint: scan every user, committing per user.

    One user's failure must not sink the whole scan, so each user runs in an
    isolated try/except with rollback. Returns the total suggestions created.
    """
    total_created = 0
    for user in user_profile_repo.list_all(db):
        try:
            total_created += len(generate_suggestions_for_user(db, user.id))
            calibrate_match_threshold(db, user.id)
            db.commit()
        except Exception:
            db.rollback()
            _log.exception("followup.scan_user_failed", user_id=user.id)
    _log.info("followup.scan_complete", total_created=total_created)
    return total_created
