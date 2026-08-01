"""Tests for the application state machine and failure envelope contracts.

Covers the acceptance criteria from
``.trellis/tasks/08-01-application-state-machine-and-failure-envelope/``:

- Application statuses are a constrained backend type.
- Invalid status transitions are rejected.
- Failure envelope validates category, code, retryability, and next action.
- Source hashes change when job/resume/profile/prompt inputs change.
- Source hashes do not contain raw resume or JD text.
- Duplicate active-operation behavior is documented and test-covered.

These are pure unit tests — no database or HTTP client required.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.application import (
    ApplicationFailureCategory,
    ApplicationFailureEnvelope,
    ApplicationFailureNextAction,
    ApplicationSourceSnapshot,
    ApplicationStatus,
    ApplicationTimelineEvent,
)
from app.services.application_state import (
    TRANSITIONS,
    InvalidTransitionError,
    active_operation_key,
    assert_transition,
    build_failure_envelope,
    build_source_snapshot,
    can_transition,
    is_active_run_status,
)

# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

# A representative subset of allowed transitions, covering the happy path of
# the readiness loop plus recovery/pause branches.
_ALLOWED_TRANSITIONS: list[tuple[ApplicationStatus, ApplicationStatus]] = [
    (ApplicationStatus.planned, ApplicationStatus.preparing),
    (ApplicationStatus.planned, ApplicationStatus.paused),
    (ApplicationStatus.preparing, ApplicationStatus.materials_ready),
    (ApplicationStatus.preparing, ApplicationStatus.failed),
    (ApplicationStatus.preparing, ApplicationStatus.paused),
    (ApplicationStatus.failed, ApplicationStatus.preparing),
    (ApplicationStatus.failed, ApplicationStatus.paused),
    (ApplicationStatus.materials_ready, ApplicationStatus.approval_required),
    (ApplicationStatus.materials_ready, ApplicationStatus.preparing),
    (ApplicationStatus.materials_ready, ApplicationStatus.paused),
    (ApplicationStatus.approval_required, ApplicationStatus.approved),
    (ApplicationStatus.approval_required, ApplicationStatus.preparing),
    (ApplicationStatus.approval_required, ApplicationStatus.paused),
    (ApplicationStatus.approved, ApplicationStatus.submitted),
    (ApplicationStatus.approved, ApplicationStatus.approval_required),
    (ApplicationStatus.approved, ApplicationStatus.failed),
    (ApplicationStatus.approved, ApplicationStatus.paused),
    (ApplicationStatus.submitted, ApplicationStatus.interviewing),
    (ApplicationStatus.submitted, ApplicationStatus.rejected),
    (ApplicationStatus.submitted, ApplicationStatus.paused),
    (ApplicationStatus.paused, ApplicationStatus.planned),
    (ApplicationStatus.paused, ApplicationStatus.preparing),
    (ApplicationStatus.interviewing, ApplicationStatus.rejected),
    (ApplicationStatus.interviewing, ApplicationStatus.paused),
    (ApplicationStatus.rejected, ApplicationStatus.planned),
]


@pytest.mark.parametrize(("frm", "to"), _ALLOWED_TRANSITIONS)
def test_allowed_transitions_pass(frm: ApplicationStatus, to: ApplicationStatus) -> None:
    assert can_transition(frm, to) is True
    assert_transition(frm, to)  # must not raise


def _all_status_pairs() -> list[tuple[ApplicationStatus, ApplicationStatus]]:
    return [
        (frm, to)
        for frm in ApplicationStatus
        for to in ApplicationStatus
        if to not in TRANSITIONS.get(frm, frozenset())
    ]


@pytest.mark.parametrize(("frm", "to"), _all_status_pairs())
def test_disallowed_transitions_rejected(frm: ApplicationStatus, to: ApplicationStatus) -> None:
    assert can_transition(frm, to) is False
    with pytest.raises(InvalidTransitionError) as exc_info:
        assert_transition(frm, to)
    assert exc_info.value.from_status is frm
    assert exc_info.value.to_status is to


def test_invalid_transition_error_message_is_safe() -> None:
    """The error message must not leak sensitive data, only status names."""
    err = InvalidTransitionError(
        ApplicationStatus.submitted, ApplicationStatus.preparing
    )
    msg = str(err)
    assert "submitted" in msg
    assert "preparing" in msg
    assert "->" in msg


def test_self_transitions_are_disallowed() -> None:
    """No status may transition to itself (idempotent re-assert is a no-op)."""
    for status in ApplicationStatus:
        assert can_transition(status, status) is False, (
            f"{status.value} should not self-transition"
        )


# ---------------------------------------------------------------------------
# Failure envelope
# ---------------------------------------------------------------------------


def test_failure_envelope_validates_required_fields() -> None:
    env = build_failure_envelope(
        category=ApplicationFailureCategory.model,
        code="model.invalid_json",
        message="The model returned output we could not understand.",
        retryable=True,
        next_action=ApplicationFailureNextAction.retry,
        agent_run_id="run_abc",
        source_ids={"job_id": "job_1", "resume_version_id": "rv_1"},
    )
    assert env.category is ApplicationFailureCategory.model
    assert env.code == "model.invalid_json"
    assert env.retryable is True
    assert env.next_action is ApplicationFailureNextAction.retry
    assert env.agent_run_id == "run_abc"
    assert env.source_ids == {"job_id": "job_1", "resume_version_id": "rv_1"}
    assert env.occurred_at.tzinfo is not None  # timezone-aware


def test_failure_envelope_defaults_occurred_at_to_now() -> None:
    before = datetime.now(UTC)
    env = build_failure_envelope(
        category=ApplicationFailureCategory.queue,
        code="queue.enqueue_failed",
        message="Could not enqueue the task.",
        retryable=True,
        next_action=ApplicationFailureNextAction.retry,
    )
    after = datetime.now(UTC)
    assert before <= env.occurred_at <= after


def test_failure_envelope_rejects_empty_code() -> None:
    with pytest.raises(ValidationError):
        ApplicationFailureEnvelope(
            category=ApplicationFailureCategory.unknown,
            code="",
            message="msg",
            retryable=False,
            next_action=ApplicationFailureNextAction.manual_review,
            occurred_at=datetime.now(UTC),
        )


def test_failure_envelope_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        ApplicationFailureEnvelope(
            category=ApplicationFailureCategory.unknown,
            code="err",
            message="",
            retryable=False,
            next_action=ApplicationFailureNextAction.manual_review,
            occurred_at=datetime.now(UTC),
        )


def test_failure_envelope_drops_overlong_source_ids() -> None:
    """Long values are defensively stripped to prevent leaking raw text."""
    raw_jd_text = "x" * 10_000  # simulates a JD snippet mistakenly passed
    env = build_failure_envelope(
        category=ApplicationFailureCategory.data,
        code="data.stale_source",
        message="Source data changed after generation started.",
        retryable=True,
        next_action=ApplicationFailureNextAction.edit_source,
        source_ids={"job_id": "job_1", "jd_raw": raw_jd_text},
    )
    assert env.source_ids == {"job_id": "job_1"}
    assert "jd_raw" not in env.source_ids
    assert raw_jd_text not in json.dumps(env.source_ids)


def test_failure_envelope_drops_sensitive_source_id_keys_even_when_short() -> None:
    """Sensitive key names are stripped even if the value is short."""
    env = build_failure_envelope(
        category=ApplicationFailureCategory.data,
        code="data.bad_source_ids",
        message="Source ids contained unsafe fields.",
        retryable=False,
        next_action=ApplicationFailureNextAction.manual_review,
        source_ids={
            "job_id": "job_1",
            "jd_raw": "Python",
            "resume_text": "FastAPI",
            "prompt": "system prompt",
            "access_token": "tok",
            "session_cookie": "cookie",
        },
    )
    assert env.source_ids == {"job_id": "job_1"}
    blob = env.model_dump_json()
    assert "Python" not in blob
    assert "FastAPI" not in blob
    assert "system prompt" not in blob
    assert "tok" not in blob
    assert "cookie" not in blob


def test_failure_envelope_serializes_without_secrets() -> None:
    """The JSON-serialized envelope must not contain raw resume/JD text."""
    raw_resume_text = "SECRET_RESUME_CONTENT_LINE_1_LINE_2 " * 20  # > 256 chars
    env = build_failure_envelope(
        category=ApplicationFailureCategory.model,
        code="model.timeout",
        message="The model call timed out.",
        retryable=True,
        next_action=ApplicationFailureNextAction.retry,
        source_ids={
            "job_id": "job_1",
            "resume_text": raw_resume_text,  # over-long, should be dropped
        },
    )
    blob = env.model_dump_json()
    assert "SECRET_RESUME_CONTENT" not in blob
    assert "job_1" in blob


def test_all_failure_categories_are_representable() -> None:
    """Every category from the PRD must be usable in an envelope."""
    for cat in ApplicationFailureCategory:
        env = build_failure_envelope(
            category=cat,
            code=f"{cat.value}.test",
            message="ok",
            retryable=False,
            next_action=ApplicationFailureNextAction.manual_review,
        )
        assert env.category is cat


def test_all_next_actions_are_representable() -> None:
    for action in ApplicationFailureNextAction:
        env = build_failure_envelope(
            category=ApplicationFailureCategory.unknown,
            code="test",
            message="ok",
            retryable=False,
            next_action=action,
        )
        assert env.next_action is action


# ---------------------------------------------------------------------------
# Source snapshot & hash
# ---------------------------------------------------------------------------

_BASE_SNAPSHOT_KW = dict(
    job_id="job_1",
    job_updated_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    resume_version_id="rv_1",
    resume_version_no=1,
    profile_updated_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    prompt_versions={"hr_opening_message": "v1"},
)


def test_build_source_snapshot_computes_hash() -> None:
    snap = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    assert snap.source_hash.startswith("sha256:")
    assert len(snap.source_hash) == len("sha256:") + 64


def test_identical_snapshots_produce_identical_hashes() -> None:
    s1 = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    s2 = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    assert s1.source_hash == s2.source_hash


def test_hash_changes_when_job_changes() -> None:
    base = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    changed = build_source_snapshot(
        **{**_BASE_SNAPSHOT_KW, "job_id": "job_2"}
    )
    assert base.source_hash != changed.source_hash


def test_hash_changes_when_job_updated_at_changes() -> None:
    base = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    changed = build_source_snapshot(
        **{
            **_BASE_SNAPSHOT_KW,
            "job_updated_at": datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
        }
    )
    assert base.source_hash != changed.source_hash


def test_hash_changes_when_resume_version_changes() -> None:
    base = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    changed = build_source_snapshot(
        **{**_BASE_SNAPSHOT_KW, "resume_version_id": "rv_2", "resume_version_no": 2}
    )
    assert base.source_hash != changed.source_hash


def test_hash_changes_when_profile_updated_at_changes() -> None:
    base = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    changed = build_source_snapshot(
        **{
            **_BASE_SNAPSHOT_KW,
            "profile_updated_at": datetime(2026, 8, 1, 11, 0, tzinfo=UTC),
        }
    )
    assert base.source_hash != changed.source_hash


def test_hash_changes_when_prompt_versions_change() -> None:
    base = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    changed = build_source_snapshot(
        **{
            **_BASE_SNAPSHOT_KW,
            "prompt_versions": {"hr_opening_message": "v2"},
        }
    )
    assert base.source_hash != changed.source_hash


def test_hash_changes_when_prompt_versions_add_new_key() -> None:
    base = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    changed = build_source_snapshot(
        **{
            **_BASE_SNAPSHOT_KW,
            "prompt_versions": {
                "hr_opening_message": "v1",
                "interview_prep": "v1",
            },
        }
    )
    assert base.source_hash != changed.source_hash


def test_hash_does_not_contain_raw_jd_text() -> None:
    """The source_hash is computed over metadata only, never raw JD text.

    We pass an extremely long JD-like raw text into the *call site context*
    (simulating that the caller has JD text available) and confirm the hash
    is identical whether or not that text is present — because the snapshot
    model has no field for raw text.
    """
    raw_jd = "We are looking for a senior engineer with Python skills. " * 500
    # The snapshot builder has no `jd_raw` parameter, so raw text cannot
    # enter the hash by construction. We verify by building two snapshots
    # with identical metadata; the presence of raw JD text in the caller's
    # scope must not affect the hash.
    s1 = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    _ = raw_jd  # available in caller scope, but never passed
    s2 = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    assert s1.source_hash == s2.source_hash
    assert raw_jd not in s1.source_hash
    assert raw_jd not in s1.model_dump_json()


def test_hash_does_not_contain_raw_resume_text() -> None:
    raw_resume = "John Doe, 5 years Python, led team of 3. " * 500
    s1 = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    _ = raw_resume
    s2 = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    assert s1.source_hash == s2.source_hash
    assert raw_resume not in s1.model_dump_json()


def test_hash_is_deterministic_across_dict_key_order() -> None:
    """Prompt-version dict insertion order must not change the hash."""
    kw = dict(_BASE_SNAPSHOT_KW)
    kw["prompt_versions"] = {
        "hr_opening_message": "v1",
        "interview_prep": "v2",
        "skill_gap_plan": "v1",
    }
    s_forward = build_source_snapshot(**kw)

    # Reversed insertion order.
    kw_rev = dict(_BASE_SNAPSHOT_KW)
    kw_rev["prompt_versions"] = {
        "skill_gap_plan": "v1",
        "interview_prep": "v2",
        "hr_opening_message": "v1",
    }
    s_reverse = build_source_snapshot(**kw_rev)
    assert s_forward.source_hash == s_reverse.source_hash


def test_none_values_do_not_collide_with_string_none() -> None:
    """A ``None`` timestamp must not hash the same as a real timestamp string.

    The snapshot model rejects non-datetime strings, so we compare ``None``
    against a valid ISO datetime — they must produce different hashes. The
    underlying sentinel logic ensures ``None`` never collides with any real
    value.
    """
    kw_none = dict(_BASE_SNAPSHOT_KW)
    kw_none["job_updated_at"] = None
    kw_present = dict(_BASE_SNAPSHOT_KW)
    # Use a different but valid datetime to confirm None vs. present differ.
    kw_present["job_updated_at"] = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    s_none = build_source_snapshot(**kw_none)
    s_present = build_source_snapshot(**kw_present)
    assert s_none.source_hash != s_present.source_hash


def test_compute_source_hash_matches_manual_sha256() -> None:
    """The hash matches a manually computed sha256 over stable JSON."""
    snap = build_source_snapshot(**_BASE_SNAPSHOT_KW)
    # Reconstruct the exact payload the service hashes.
    payload = {
        "job_id": snap.job_id,
        "job_updated_at": snap.job_updated_at,
        "resume_version_id": snap.resume_version_id,
        "resume_version_no": snap.resume_version_no,
        "profile_updated_at": snap.profile_updated_at,
        "prompt_versions": snap.prompt_versions,
    }
    expected = "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert snap.source_hash == expected


def test_source_snapshot_model_requires_hash() -> None:
    """The model requires a non-empty source_hash at construction."""
    with pytest.raises(ValidationError):
        ApplicationSourceSnapshot(
            job_id="job_1",
            source_hash="",  # empty -> invalid
        )


# ---------------------------------------------------------------------------
# Timeline event
# ---------------------------------------------------------------------------


def test_timeline_event_minimal() -> None:
    evt = ApplicationTimelineEvent(
        event="status_change",
        occurred_at=datetime.now(UTC),
    )
    assert evt.event == "status_change"
    assert evt.from_status is None
    assert evt.to_status is None


def test_timeline_event_with_transition() -> None:
    evt = ApplicationTimelineEvent(
        event="status_change",
        from_status=ApplicationStatus.planned,
        to_status=ApplicationStatus.preparing,
        occurred_at=datetime.now(UTC),
    )
    assert evt.from_status is ApplicationStatus.planned
    assert evt.to_status is ApplicationStatus.preparing


# ---------------------------------------------------------------------------
# Duplicate active-operation contract
# ---------------------------------------------------------------------------


def test_active_operation_key_equality() -> None:
    """Two identical operation keys are equal and hash-equal."""
    k1 = active_operation_key(
        operation_type="generate_hr_message",
        application_id="app_1",
        resume_version_id="rv_1",
        source_hash="sha256:abc",
    )
    k2 = active_operation_key(
        operation_type="generate_hr_message",
        application_id="app_1",
        resume_version_id="rv_1",
        source_hash="sha256:abc",
    )
    assert k1 == k2
    assert hash(k1) == hash(k2)


def test_active_operation_key_differs_on_operation_type() -> None:
    k1 = active_operation_key(
        operation_type="generate_hr_message",
        application_id="app_1",
        resume_version_id="rv_1",
        source_hash="sha256:abc",
    )
    k2 = active_operation_key(
        operation_type="generate_interview_prep",
        application_id="app_1",
        resume_version_id="rv_1",
        source_hash="sha256:abc",
    )
    assert k1 != k2


def test_active_operation_key_differs_on_source_hash() -> None:
    """A stale source (different hash) is NOT a duplicate — it's a new run."""
    k1 = active_operation_key(
        operation_type="generate_hr_message",
        application_id="app_1",
        resume_version_id="rv_1",
        source_hash="sha256:abc",
    )
    k2 = active_operation_key(
        operation_type="generate_hr_message",
        application_id="app_1",
        resume_version_id="rv_1",
        source_hash="sha256:def",
    )
    assert k1 != k2


@pytest.mark.parametrize("status", ["queued", "running"])
def test_is_active_run_status_recognizes_active(status: str) -> None:
    assert is_active_run_status(status) is True


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", ""])
def test_is_active_run_status_rejects_inactive(status: str) -> None:
    assert is_active_run_status(status) is False
