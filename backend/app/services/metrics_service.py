"""Funnel metrics service (Phase 1 feedback loop).

Computes the user-scoped submission funnel from application records and
outcome events. This is the calibration surface: reply/interview rates by
match-score bucket tell us whether ``COMMUNICATE_MIN_SCORE`` and the opening
message prompts are actually predictive — replacing guesswork with evidence.

Definitions:

- **submitted**: applications whose status reached ``submitted``,
  ``interviewing``, or ``rejected`` (i.e. they left the preparation pipeline).
- **with_reply**: submitted applications with any ``replied``/``interview``/
  ``offer`` outcome (interviews and offers imply a reply happened).
- Rates are ``None`` when the denominator is zero (never divide by zero).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.models import UserProfile
from app.db.repositories import (
    application_outcome_repo,
    application_repo,
    generated_artifact_repo,
)
from app.schemas.application import ApplicationStatus
from app.schemas.outcome import (
    FunnelMetricsOut,
    MatchScoreBucketOut,
    OpeningPromptBucketOut,
    OutcomeType,
)

#: Statuses proving the application actually reached the platform.
_SUBMITTED_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {ApplicationStatus.submitted, ApplicationStatus.interviewing, ApplicationStatus.rejected}
)

#: Outcomes that imply HR replied at some point.
_REPLY_OUTCOMES: frozenset[OutcomeType] = frozenset(
    {OutcomeType.replied, OutcomeType.interview, OutcomeType.offer}
)

_INTERVIEW_OUTCOMES: frozenset[OutcomeType] = frozenset(
    {OutcomeType.interview, OutcomeType.offer}
)

#: Match-score bucket boundaries (calibration view, mirrors the 0.6 gate).
_BUCKET_ORDER: tuple[str, ...] = ("<0.6", "0.6-0.8", ">=0.8", "unknown")


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score < 0.6:
        return "<0.6"
    if score < 0.8:
        return "0.6-0.8"
    return ">=0.8"


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def funnel_metrics(db: Session, current_user: UserProfile) -> FunnelMetricsOut:
    """Compute the full funnel for the current user."""
    records = application_repo.list_all_for_user(db, current_user.id)
    outcomes = application_outcome_repo.list_for_user(db, current_user.id)
    job_ids = sorted({r.job_id for r in records})
    scores = application_outcome_repo.latest_match_scores_by_job(db, job_ids)
    opening_prompts = generated_artifact_repo.latest_opening_prompt_versions_by_job(
        db, current_user.id, job_ids
    )

    # Per-application outcome flags (latest events win implicitly: any
    # qualifying event counts — an interview implies a reply).
    replied_ids: set[str] = set()
    interview_ids: set[str] = set()
    rejected_ids: set[str] = set()
    offer_ids: set[str] = set()
    for outcome in outcomes:
        otype = OutcomeType(outcome.outcome_type)
        if otype in _REPLY_OUTCOMES:
            replied_ids.add(outcome.application_id)
        if otype in _INTERVIEW_OUTCOMES:
            interview_ids.add(outcome.application_id)
        if otype is OutcomeType.rejected:
            rejected_ids.add(outcome.application_id)
        if otype is OutcomeType.offer:
            offer_ids.add(outcome.application_id)

    submitted_records = [r for r in records if ApplicationStatus(r.status) in _SUBMITTED_STATUSES]

    # Bucket submitted applications by the job's latest match score.
    buckets: dict[str, dict[str, int]] = {
        name: {"applications": 0, "replied": 0, "interviews": 0} for name in _BUCKET_ORDER
    }
    # ... and by the job's latest opening-message prompt version.
    prompt_buckets: dict[str, dict[str, int]] = {}
    for record in submitted_records:
        bucket = buckets[_score_bucket(scores.get(record.job_id))]
        bucket["applications"] += 1
        prompt_version = opening_prompts.get(record.job_id) or "unknown"
        pbucket = prompt_buckets.setdefault(
            prompt_version, {"applications": 0, "replied": 0, "interviews": 0}
        )
        pbucket["applications"] += 1
        if record.id in replied_ids:
            bucket["replied"] += 1
            pbucket["replied"] += 1
        if record.id in interview_ids:
            bucket["interviews"] += 1
            pbucket["interviews"] += 1

    with_reply = len(replied_ids & {r.id for r in submitted_records})
    interviews = len(interview_ids & {r.id for r in submitted_records})

    return FunnelMetricsOut(
        applications_total=len(records),
        submitted=len(submitted_records),
        with_reply=with_reply,
        interviews=interviews,
        offers=len(offer_ids & {r.id for r in submitted_records}),
        rejected=len(rejected_ids & {r.id for r in submitted_records}),
        reply_rate=_rate(with_reply, len(submitted_records)),
        interview_rate=_rate(interviews, len(submitted_records)),
        by_match_score=[
            MatchScoreBucketOut(bucket=name, **buckets[name])
            for name in _BUCKET_ORDER
            if buckets[name]["applications"] > 0
        ],
        by_opening_prompt=[
            OpeningPromptBucketOut(prompt_version=version, **prompt_buckets[version])
            for version in sorted(prompt_buckets)
            if prompt_buckets[version]["applications"] > 0
        ],
    )
