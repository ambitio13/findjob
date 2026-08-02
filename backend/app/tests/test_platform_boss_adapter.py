"""Tests for the platform adapter boundary and the fake BOSS adapter.

These tests prove the typed boundary in :mod:`app.platforms.base` classifies
every prepare/submit outcome correctly, the fake adapter is deterministic, and
the registry selects the fake adapter by default (real adapter only behind the
explicit ``BOSS_ADAPTER_ENABLED`` flag).

The real Playwright-backed adapter is env-gated and not exercised here; its
safety invariants are documented in ``app/platforms/boss/adapter.py`` and
audited by the failure-matrix tests in ``test_platform_submission_api.py``.
"""

from __future__ import annotations

import pytest

from app.platforms.base import (
    FilledAttachment,
    FilledField,
    FilledPageState,
    FilledSubmissionSnapshot,
    PlatformAdapter,
    PrepareContext,
    PrepareOutcome,
    SubmitContext,
    SubmitOutcome,
    prepare_failure_code,
    prepare_failure_next_action,
    submit_failure_code,
    submit_failure_next_action,
)
from app.platforms.boss.fake_adapter import FakeBossAdapter
from app.platforms.boss.registry import get_adapter


def _ctx(scenario: str | None = None, **overrides: object) -> PrepareContext:
    base: dict[str, object] = {
        "application_id": "app-1",
        "target_platform": "boss",
        "target_resource": "https://boss.zhipin.com/job/123",
        "selected_artifact_ids": ["art-1"],
        "outgoing_text": "您好，我对这个岗位很感兴趣。",
        "resume_file_reference": "resume-v1",
        "source_hash": "sha256:abc",
        "session_reference": "opaque-session-handle",
    }
    base.update(overrides)
    return PrepareContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Boundary types
# ---------------------------------------------------------------------------


def test_protocol_is_runtime_checkable() -> None:
    """The fake adapter satisfies the runtime-checkable PlatformAdapter protocol."""
    adapter = FakeBossAdapter()
    assert isinstance(adapter, PlatformAdapter)


def test_filled_snapshot_round_trips_and_is_sanitized() -> None:
    """A filled snapshot serializes without secrets and preserves safe fields."""
    from datetime import UTC, datetime

    snapshot = FilledSubmissionSnapshot(
        target_platform="boss",
        target_resource="job-123",
        application_id="app-1",
        selected_artifact_ids=["art-1"],
        resume_file_reference="resume-v1",
        fields=[
            FilledField(
                name="message",
                label="开场白",
                value="safe visible text",
                source_artifact_id="art-1",
            )
        ],
        attachments=[
            FilledAttachment(
                kind="resume",
                display_name="resume.pdf",
                reference="resume-v1",
            )
        ],
        page_state=FilledPageState(
            url_hash="sha256:abc",
            title="BOSS application form",
            final_submit_selector_seen=True,
        ),
        captured_at=datetime.now(UTC),
    )
    dumped = snapshot.model_dump(mode="json")
    assert dumped["target_platform"] == "boss"
    assert dumped["fields"][0]["value"] == "safe visible text"
    # No secret-bearing keys may exist anywhere in the snapshot.
    blob = repr(dumped)
    for secret in ("cookie", "token", "password", "session_secret", "raw_jd", "raw_resume"):
        assert secret not in blob.lower()


# ---------------------------------------------------------------------------
# Prepare outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [
        PrepareOutcome.login_required,
        PrepareOutcome.captcha_required,
        PrepareOutcome.selector_drift,
        PrepareOutcome.rate_limited,
        PrepareOutcome.duplicate_detected,
        PrepareOutcome.upload_failed,
        PrepareOutcome.unknown,
    ],
)
async def test_fake_adapter_prepare_returns_every_failure_outcome(
    outcome: PrepareOutcome,
) -> None:
    """Each non-filled prepare outcome is classified with a sanitized failure code."""
    adapter = FakeBossAdapter(scenario=outcome.value)
    result = await adapter.prepare_submission(_ctx())
    assert result.outcome is outcome
    assert result.snapshot is None
    assert result.failure_code == prepare_failure_code(outcome)
    assert prepare_failure_next_action(outcome) in {"manual_review", "retry"}


async def test_fake_adapter_prepare_filled_preview_carries_sanitized_snapshot() -> None:
    """The filled_preview outcome carries a snapshot built from the context."""
    adapter = FakeBossAdapter(scenario="filled_preview")
    result = await adapter.prepare_submission(_ctx())
    assert result.outcome is PrepareOutcome.filled_preview
    assert result.snapshot is not None
    assert result.snapshot.target_platform == "boss"
    assert result.snapshot.application_id == "app-1"
    assert result.snapshot.fields[0].name == "message"
    assert result.snapshot.fields[0].value == "您好，我对这个岗位很感兴趣。"
    assert result.snapshot.attachments[0].kind == "resume"
    assert result.snapshot.page_state.final_submit_selector_seen is True


async def test_fake_adapter_prepare_records_call_for_audit() -> None:
    """The adapter records each prepare call so tests can assert it was invoked."""
    adapter = FakeBossAdapter()
    ctx = _ctx()
    await adapter.prepare_submission(ctx)
    assert adapter.prepare_calls == [ctx]


# ---------------------------------------------------------------------------
# Submit outcomes
# ---------------------------------------------------------------------------


async def test_fake_adapter_submit_submitted_returns_platform_reference() -> None:
    adapter = FakeBossAdapter(scenario="submitted", platform_reference="boss-ref-xyz")
    snapshot = (await adapter.prepare_submission(_ctx())).snapshot
    assert snapshot is not None
    result = await adapter.submit_prepared(
        SubmitContext(
            application_id="app-1",
            target_platform="boss",
            target_resource="job-123",
            filled_snapshot=snapshot,
            session_reference="opaque",
        )
    )
    assert result.outcome is SubmitOutcome.submitted
    assert result.platform_reference == "boss-ref-xyz"
    assert result.failure_code is None


@pytest.mark.parametrize(
    "scenario, expected_outcome",
    [
        ("submit_duplicate_detected", SubmitOutcome.duplicate_detected),
        ("submit_unknown", SubmitOutcome.unknown),
        ("platform_failure", SubmitOutcome.platform_failure),
    ],
)
async def test_fake_adapter_submit_returns_every_failure_outcome(
    scenario: str, expected_outcome: SubmitOutcome
) -> None:
    adapter = FakeBossAdapter(scenario=scenario)
    snapshot = (await adapter.prepare_submission(_ctx())).snapshot
    assert snapshot is not None
    result = await adapter.submit_prepared(
        SubmitContext(
            application_id="app-1",
            target_platform="boss",
            target_resource="job-123",
            filled_snapshot=snapshot,
        )
    )
    assert result.outcome is expected_outcome
    assert result.failure_code == submit_failure_code(expected_outcome)
    assert submit_failure_next_action(expected_outcome) == "manual_review"


async def test_fake_adapter_submit_records_call_for_audit() -> None:
    adapter = FakeBossAdapter(scenario="submitted")
    snapshot = (await adapter.prepare_submission(_ctx())).snapshot
    assert snapshot is not None
    ctx = SubmitContext(
        application_id="app-1",
        target_platform="boss",
        target_resource="job-123",
        filled_snapshot=snapshot,
    )
    await adapter.submit_prepared(ctx)
    assert adapter.submit_calls == [ctx]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_returns_fake_adapter_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOSS_ADAPTER_ENABLED", raising=False)
    adapter = get_adapter()
    assert isinstance(adapter, FakeBossAdapter)


def test_registry_forwards_scenario_to_fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOSS_ADAPTER_ENABLED", raising=False)
    adapter = get_adapter(scenario="captcha_required")
    assert isinstance(adapter, FakeBossAdapter)
    assert adapter.scenario == "captcha_required"


def test_registry_real_adapter_only_behind_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the flag selects the real adapter path, never the fake adapter."""
    monkeypatch.setenv("BOSS_ADAPTER_ENABLED", "1")
    try:
        adapter = get_adapter()
    except RuntimeError as exc:
        # Local/dev installs may not have Playwright installed yet. That still
        # proves the registry honored the flag and attempted the real adapter.
        assert "playwright" in str(exc).lower()
    else:
        assert not isinstance(adapter, FakeBossAdapter)
        assert adapter.platform == "boss"
