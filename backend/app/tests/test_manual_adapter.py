"""Tests for the Phase 4 manual adapter + generate-and-copy contract.

Covers:

- :class:`ManualPlatformAdapter` satisfies the ``PlatformAdapter`` protocol
  and never touches a browser: prepare builds an in-memory filled preview,
  submit/communicate return the designed ``manual_handoff`` envelope instead
  of performing any platform action.
- :func:`get_adapter_for_platform` routes ``boss`` to the BOSS adapter family
  and every other platform (``manual``, lagou, …) to the manual adapter —
  manual is a first-class citizen, not a fallback.
- Multi-platform JD entry: ``source_url`` (the pasted listing link) is
  accepted by create + parse + PATCH and persisted on the job row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.models import JobPosting, UserProfile
from app.db.session import SessionLocal
from app.platforms import ManualPlatformAdapter, get_adapter_for_platform
from app.platforms.base import (
    CommunicationExecuteContext,
    CommunicationOutcome,
    PlatformAdapter,
    PrepareContext,
    PrepareOutcome,
    SubmitContext,
    SubmitOutcome,
)
from app.platforms.boss.fake_adapter import FakeBossAdapter
from app.platforms.manual.adapter import MANUAL_HANDOFF_CODE


def _prepare_ctx(outgoing_text: str | None = "您好，我对该岗位很感兴趣。") -> PrepareContext:
    return PrepareContext(
        application_id="app-manual-1",
        target_platform="lagou",
        target_resource="https://example.com/job/1",
        selected_artifact_ids=["artifact-1"],
        outgoing_text=outgoing_text,
        source_hash="hash-1",
    )


async def test_manual_adapter_satisfies_platform_protocol() -> None:
    adapter = ManualPlatformAdapter()
    assert isinstance(adapter, PlatformAdapter)
    assert adapter.platform == "manual"


async def test_manual_prepare_builds_in_memory_filled_preview() -> None:
    adapter = ManualPlatformAdapter()
    result = await adapter.prepare_submission(_prepare_ctx())
    assert result.outcome is PrepareOutcome.filled_preview
    assert result.failure_code is None
    snapshot = result.snapshot
    assert snapshot is not None
    assert snapshot.target_platform == "lagou"
    assert snapshot.application_id == "app-manual-1"
    assert len(snapshot.fields) == 1
    field = snapshot.fields[0]
    assert field.name == "message"
    assert field.value == "您好，我对该岗位很感兴趣。"
    assert field.source_artifact_id == "artifact-1"


async def test_manual_prepare_rejects_oversized_message() -> None:
    adapter = ManualPlatformAdapter()
    result = await adapter.prepare_submission(_prepare_ctx("x" * 2001))
    assert result.outcome is PrepareOutcome.unknown
    assert result.failure_code == "message_too_long"
    assert result.snapshot is None


async def test_manual_submit_is_a_designed_handoff_not_an_error() -> None:
    adapter = ManualPlatformAdapter()
    prepare = await adapter.prepare_submission(_prepare_ctx())
    assert prepare.snapshot is not None
    submit = await adapter.submit_prepared(
        SubmitContext(
            application_id="app-manual-1",
            target_platform="lagou",
            target_resource="https://example.com/job/1",
            filled_snapshot=prepare.snapshot,
        )
    )
    # unknown = system cannot verify a manual paste; manual_handoff marks it
    # as a designed human hand-off (never auto-retried).
    assert submit.outcome is SubmitOutcome.unknown
    assert submit.failure_code == MANUAL_HANDOFF_CODE
    assert submit.platform_reference is None


async def test_manual_communicate_is_a_designed_handoff() -> None:
    adapter = ManualPlatformAdapter()
    result = await adapter.execute_communication(
        CommunicationExecuteContext(
            application_id="app-manual-1",
            target_platform="lagou",
            target_resource="hash-1",
            opening_message="您好",
            source_hash="hash-1",
        )
    )
    assert result.outcome is CommunicationOutcome.unknown
    assert result.failure_code == MANUAL_HANDOFF_CODE


def test_resolver_routes_boss_to_boss_family() -> None:
    # No env flags in tests → the BOSS fake adapter is active.
    adapter = get_adapter_for_platform("boss")
    assert isinstance(adapter, FakeBossAdapter)


@pytest.mark.parametrize("platform", ["manual", "lagou", "zhipin", "unknown"])
def test_resolver_routes_other_platforms_to_manual(platform: str) -> None:
    adapter = get_adapter_for_platform(platform)
    assert isinstance(adapter, ManualPlatformAdapter)


# ---------------------------------------------------------------------------
# Multi-platform job entry: pasted JD link (source_url)
# ---------------------------------------------------------------------------


def _make_user(user_id: str) -> None:
    with SessionLocal() as db:
        db.add(UserProfile(id=user_id, display_name="P4 用户"))
        db.commit()


def test_create_job_persists_source_url(client: TestClient) -> None:
    _make_user("p4_create")
    resp = client.post(
        "/api/v1/jobs",
        json={
            "company": "某公司",
            "title": "后端工程师",
            "jd_raw": "负责后端开发。",
            "platform": "lagou",
            "source_url": "https://www.lagou.com/jobs/123.html",
        },
        headers={"X-User-Id": "p4_create"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_url"] == "https://www.lagou.com/jobs/123.html"

    with SessionLocal() as db:
        row = db.execute(
            select(JobPosting).where(JobPosting.user_id == "p4_create")
        ).scalar_one()
        assert row.source_url == "https://www.lagou.com/jobs/123.html"


def test_parse_job_persists_source_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, patch

    _make_user("p4_parse")
    pool = AsyncMock()
    pool.enqueue_job.return_value = object()
    with patch("app.queue.runtime.get_queue", new=AsyncMock(return_value=pool)):
        resp = client.post(
            "/api/v1/jobs/parse",
            json={
                "raw_jd": "高级后端工程师，要求 Python。",
                "platform": "zhipin",
                "source_url": "https://www.zhipin.com/job_detail/abc.html",
            },
            headers={"X-User-Id": "p4_parse"},
        )
    assert resp.status_code == 202, resp.text

    with SessionLocal() as db:
        row = db.execute(
            select(JobPosting).where(JobPosting.user_id == "p4_parse")
        ).scalar_one()
        assert row.source_url == "https://www.zhipin.com/job_detail/abc.html"
        assert row.platform == "zhipin"


def test_patch_job_can_set_and_clear_source_url(client: TestClient) -> None:
    _make_user("p4_patch")
    created = client.post(
        "/api/v1/jobs",
        json={"company": "A", "title": "B", "jd_raw": "JD 文本。"},
        headers={"X-User-Id": "p4_patch"},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    assert created.json()["source_url"] is None

    # Set the link.
    set_resp = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"source_url": "https://example.com/job/9"},
        headers={"X-User-Id": "p4_patch"},
    )
    assert set_resp.status_code == 200, set_resp.text
    assert set_resp.json()["source_url"] == "https://example.com/job/9"

    # Explicit null clears it without touching other fields.
    clear_resp = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"source_url": None},
        headers={"X-User-Id": "p4_patch"},
    )
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["source_url"] is None
    assert clear_resp.json()["company"] == "A"
