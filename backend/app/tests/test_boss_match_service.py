"""Service integration tests for the BOSS match-decision workflow.

Covers:

- Happy path: valid context → match decision persisted as ``GeneratedArtifact``,
  six ordered ``AgentStep`` rows, run succeeded.
- Cross-user job → 404.
- Missing resume version → 404.
- Empty resume ``raw_text`` → 422.
- Model call failure → run failed, 502.
- Invalid model JSON → run failed, 502, "Invalid model JSON never becomes a
  communicate decision."
- Low-score communicate → downgraded to needs_review, artifact persisted.
- Missing-requirements communicate → downgraded to needs_review.
- Artifact ``source_ids`` contains provenance (prompt_version, model, provider,
  workflow_type).
- Re-running match for same job creates a new artifact (no dedup at artifact
  level).

All tests use the ``client`` fixture so the DB is truncated per test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models.models import (
    AgentRun,
    AgentStep,
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.session import SessionLocal
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.models_gateway.fake import _BOSS_MATCH_FAKE_OUTPUTS, FakeModelGateway
from app.services.boss_match_service import (
    WORKFLOW_TYPE,
    load_boss_match_context,
    run_boss_match_decision,
)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "match_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="Match 用户")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session,
    user_id: str,
    raw_text: str = "张三\nPython 5年 FastAPI",
    filename: str = "r.txt",
) -> tuple[Resume, ResumeVersion]:
    resume = Resume(user_id=user_id, filename=filename)
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        version_no=1,
        raw_text=raw_text,
        parsed_facts={"_parser": "text", "_parser_status": "parsed"},
    )
    db.add(version)
    db.flush()
    return resume, version


def _make_job(
    db: Session,
    user_id: str,
    jd_raw: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> JobPosting:
    job = JobPosting(
        user_id=user_id,
        company="Acme",
        title="Backend Engineer",
        jd_raw=jd_raw,
    )
    db.add(job)
    db.flush()
    return job


def _seed(
    user_id: str = "match_user",
    *,
    raw_text: str = "张三\nPython 5年 FastAPI",
    jd_raw: str = "Senior Python backend engineer. Build APIs with FastAPI.",
) -> dict[str, str]:
    """Seed a user + resume + version + job, returning their IDs."""
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume, version = _make_resume(db, user_id, raw_text)
        job = _make_job(db, user_id, jd_raw=jd_raw)
        db.commit()
        return {
            "user_id": user_id,
            "resume_id": resume.id,
            "resume_version_id": version.id,
            "job_id": job.id,
        }


def _get_user(user_id: str) -> UserProfile:
    with SessionLocal() as db:
        user = db.get(UserProfile, user_id)
        assert user is not None
        return user


def _patched_communicate(output: dict[str, Any]) -> dict[str, Any]:
    """Return a fake-outputs dict with ``communicate`` replaced by ``output``."""
    return {
        "communicate": output,
        "skip": _BOSS_MATCH_FAKE_OUTPUTS["skip"],
        "needs_review": _BOSS_MATCH_FAKE_OUTPUTS["needs_review"],
    }


# ---------------------------------------------------------------------------
# Stub gateways
# ---------------------------------------------------------------------------


class _StubGateway(ModelGateway):
    """Gateway returning a fixed content string."""

    provider_name = "stub"

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=self._content,
            model="stub-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=0,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


class _ErrorGateway(ModelGateway):
    """Gateway that always raises."""

    provider_name = "error"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("simulated provider outage")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_persists_artifact_and_steps(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        run, artifact, execution, downgraded, _draft = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )

    assert run.status == "succeeded"
    assert run.workflow_type == WORKFLOW_TYPE
    assert artifact.artifact_type == "boss_match_decision"
    assert execution.output.decision.value in ("communicate", "skip", "needs_review")
    assert not downgraded  # fake communicate output has high score + valid message

    # Verify six ordered steps.
    with SessionLocal() as db:
        steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run.id)
            .order_by(AgentStep.step_no)
            .all()
        )
    assert len(steps) == 6
    assert steps[0].name == "load_context"
    assert steps[1].name == "build_prompt_context"
    assert steps[2].name == "call_model"
    assert steps[3].name == "validate_model_output"
    assert steps[4].name == "persist_outputs"
    assert steps[5].name == "complete_run"
    assert all(s.status == "succeeded" for s in steps)


@pytest.mark.asyncio
async def test_artifact_source_ids_contain_provenance(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        run, artifact, _, _, _ = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )

    source_ids = artifact.source_ids
    assert source_ids["prompt_version"] == "boss-match-decision-v1"
    assert source_ids["provider"] == "fake"
    assert source_ids["model"] == "fake-model"
    assert source_ids["workflow_type"] == WORKFLOW_TYPE
    assert source_ids["job_id"] == ids["job_id"]
    assert source_ids["resume_version_id"] == ids["resume_version_id"]
    assert source_ids["user_id"] == ids["user_id"]
    assert source_ids["safety_downgraded"] is False
    # No raw text in source_ids.
    assert "raw_text" not in source_ids
    assert "jd_raw" not in source_ids


@pytest.mark.asyncio
async def test_artifact_content_is_valid_match_decision_json(client) -> None:
    from app.schemas.boss_match_decision import MatchDecisionModelOutput

    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        _, artifact, _, _, _ = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )

    output = MatchDecisionModelOutput.model_validate_json(artifact.content)
    assert output.decision.value in ("communicate", "skip", "needs_review")


# ---------------------------------------------------------------------------
# Context loader errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_job_returns_404(client) -> None:
    ids = _seed()
    # Seed a second user.
    with SessionLocal() as db:
        other = UserProfile(id="other_user", display_name="Other")
        db.add(other)
        db.commit()

    other = _get_user("other_user")

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            load_boss_match_context(db, other, ids["job_id"], ids["resume_version_id"])

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_resume_version_returns_404(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            load_boss_match_context(db, user, ids["job_id"], "nonexistent_version")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_empty_resume_text_returns_422(client) -> None:
    ids = _seed(raw_text="   ")
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            load_boss_match_context(db, user, ids["job_id"], ids["resume_version_id"])

    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Model call failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_call_failure_returns_502_and_fails_run(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await run_boss_match_decision(
                db,
                user,
                job_id=ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                gateway=_ErrorGateway(),
            )

    assert exc_info.value.status_code == 502

    with SessionLocal() as db:
        run = (
            db.query(AgentRun)
            .filter(
                AgentRun.user_id == ids["user_id"],
                AgentRun.workflow_type == WORKFLOW_TYPE,
            )
            .order_by(AgentRun.created_at.desc())
            .first()
        )
        assert run is not None
        assert run.status == "failed"
        assert run.error == "model call failed"

        # No artifact should be persisted on failure.
        artifacts = (
            db.query(GeneratedArtifact).filter(GeneratedArtifact.agent_run_id == run.id).all()
        )
        assert len(artifacts) == 0


# ---------------------------------------------------------------------------
# Invalid model JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_model_json_returns_502_and_never_communicate(client) -> None:
    """Invalid model JSON never becomes a communicate decision."""
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await run_boss_match_decision(
                db,
                user,
                job_id=ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                gateway=_StubGateway(content="not json {"),
            )

    assert exc_info.value.status_code == 502

    with SessionLocal() as db:
        run = (
            db.query(AgentRun)
            .filter(
                AgentRun.user_id == ids["user_id"],
                AgentRun.workflow_type == WORKFLOW_TYPE,
            )
            .order_by(AgentRun.created_at.desc())
            .first()
        )
        assert run is not None
        assert run.status == "failed"
        assert "invalid" in run.error.lower()

        artifacts = (
            db.query(GeneratedArtifact).filter(GeneratedArtifact.agent_run_id == run.id).all()
        )
        assert len(artifacts) == 0


# ---------------------------------------------------------------------------
# Safety gate downgrades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_score_communicate_downgraded_to_needs_review(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    # Patch the fake output to return a low-score communicate.
    bad_output = {
        "decision": "communicate",
        "score": 0.45,
        "reasons": ["Weak match"],
        "risks": [],
        "missing_requirements": [],
        "opening_message": "您好，我对该职位非常感兴趣，希望能进一步沟通。",
    }

    with patch(
        "app.models_gateway.fake._BOSS_MATCH_FAKE_OUTPUTS",
        _patched_communicate(bad_output),
    ):
        with SessionLocal() as db:
            run, artifact, execution, downgraded, _draft = await run_boss_match_decision(
                db,
                user,
                job_id=ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                gateway=FakeModelGateway(),
            )

    assert downgraded is True
    assert execution.output.decision.value == "needs_review"
    assert execution.output.opening_message is None
    assert run.status == "succeeded"
    assert artifact.source_ids["safety_downgraded"] is True


@pytest.mark.asyncio
async def test_missing_requirements_communicate_downgraded(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    bad_output = {
        "decision": "communicate",
        "score": 0.82,
        "reasons": ["Strong skills match"],
        "risks": [],
        "missing_requirements": ["Kafka production experience"],
        "opening_message": "您好，我对该职位非常感兴趣，希望能进一步沟通。",
    }

    with patch(
        "app.models_gateway.fake._BOSS_MATCH_FAKE_OUTPUTS",
        _patched_communicate(bad_output),
    ):
        with SessionLocal() as db:
            run, artifact, execution, downgraded, _draft = await run_boss_match_decision(
                db,
                user,
                job_id=ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                gateway=FakeModelGateway(),
            )

    assert downgraded is True
    assert execution.output.decision.value == "needs_review"
    assert execution.output.opening_message is None


@pytest.mark.asyncio
async def test_pii_opening_message_downgraded(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    bad_output = {
        "decision": "communicate",
        "score": 0.82,
        "reasons": ["Strong match"],
        "risks": [],
        "missing_requirements": [],
        "opening_message": "您好，我的手机号是13812345678，请联系我。",
    }

    with patch(
        "app.models_gateway.fake._BOSS_MATCH_FAKE_OUTPUTS",
        _patched_communicate(bad_output),
    ):
        with SessionLocal() as db:
            run, artifact, execution, downgraded, _draft = await run_boss_match_decision(
                db,
                user,
                job_id=ids["job_id"],
                resume_version_id=ids["resume_version_id"],
                gateway=FakeModelGateway(),
            )

    assert downgraded is True
    assert execution.output.decision.value == "needs_review"
    assert execution.output.opening_message is None


# ---------------------------------------------------------------------------
# Re-run creates new artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_creates_new_artifact(client) -> None:
    ids = _seed()
    user = _get_user(ids["user_id"])

    with SessionLocal() as db:
        run1, artifact1, _, _, _ = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )
    with SessionLocal() as db:
        run2, artifact2, _, _, _ = await run_boss_match_decision(
            db,
            user,
            job_id=ids["job_id"],
            resume_version_id=ids["resume_version_id"],
            gateway=FakeModelGateway(),
        )

    assert run1.id != run2.id
    assert artifact1.id != artifact2.id
