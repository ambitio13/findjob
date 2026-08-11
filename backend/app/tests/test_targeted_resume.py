"""Tests for the ``targeted_resume`` readiness artifact.

Covers the Phase-2 JD-targeted resume chain:

- ``enumerate_resume_facts``: the shared fact-numbering used by both the
  prompt builder and the traceability validation (single source of truth).
- Prompt builder: the ``## NUMBERED RESUME FACTS`` section is injected only
  for ``targeted_resume``.
- Executor traceability gate: schema-valid but untraceable outputs are
  rejected (kind ``"traceability"``) so no artifact is persisted.
- Worker end-to-end: success with extracted facts, non-retryable failure when
  no facts exist, retryable failure when the model cites unknown fact refs.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.agents.prompts.readiness import (
    ReadinessContext,
    build_readiness_messages,
    enumerate_resume_facts,
)
from app.agents.readiness_executor import (
    ReadinessExecutor,
    ReadinessValidationError,
)
from app.db.models.models import (
    AgentRun,
    AgentStep,
    ApplicationRecord,
    GeneratedArtifact,
    Resume,
    ResumeVersion,
)
from app.db.session import SessionLocal
from app.models_gateway.base import ChatRequest, ChatResponse, ChatUsage, ModelGateway
from app.queue.handlers import readiness_generation
from app.tests.test_readiness_artifacts import (
    _compute_source_hash_for,
    _create_queued_readiness_run,
    _make_application,
    _make_job,
    _make_payload,
    _make_user,
)

_FACTS = {
    "education": [
        {"school": "某大学", "degree": "本科", "major": "计算机科学", "period": "2016-2020"},
    ],
    "work_experience": [
        {"company": "某公司", "title": "后端工程师", "period": "2020-至今"},
    ],
    "projects": [
        {"name": "简历投递 Agent", "role": "后端", "summary": "构建自动化投递系统。"},
    ],
    "skills": ["Python", "FastAPI", "PostgreSQL"],
}


# ---------------------------------------------------------------------------
# Fact enumeration (shared by prompt builder + traceability validation)
# ---------------------------------------------------------------------------


def test_enumerate_resume_facts_assigns_section_ids() -> None:
    numbered = enumerate_resume_facts(_FACTS)
    assert set(numbered) == {"E1", "W1", "P1", "S1", "S2", "S3"}
    assert "某大学" in numbered["E1"]
    assert numbered["S1"] == "skills: Python"


def test_enumerate_resume_facts_empty_when_no_facts() -> None:
    assert enumerate_resume_facts(None) == {}
    assert enumerate_resume_facts({}) == {}
    assert enumerate_resume_facts({"education": None, "skills": "not-a-list"}) == {}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _context(artifact_type: str, facts: dict | None) -> ReadinessContext:
    return ReadinessContext(
        user_id="tr_prompt",
        artifact_type=artifact_type,
        profile={"display_name": "张三"},
        job={"id": "job1", "company": "Acme", "title": "Backend", "jd_raw": "需要Python"},
        resume={
            "resume_id": "r1",
            "resume_version_id": "v1",
            "raw_text": "张三 Python 5年",
            "facts": facts or {},
        },
        source_hash="hash",
    )


def test_prompt_includes_numbered_facts_only_for_targeted_resume() -> None:
    targeted = build_readiness_messages(_context("targeted_resume", _FACTS))
    user_msg = targeted.messages[1].content
    assert "## NUMBERED RESUME FACTS" in user_msg
    assert "- [E1]" in user_msg
    assert "- [P1]" in user_msg
    assert "source_fact_refs" in user_msg

    other = build_readiness_messages(_context("hr_opening_message", _FACTS))
    assert "## NUMBERED RESUME FACTS" not in other.messages[1].content


def test_prompt_targeted_resume_without_facts_warns_model() -> None:
    result = build_readiness_messages(_context("targeted_resume", {}))
    assert "no structured resume facts available" in result.messages[1].content


# ---------------------------------------------------------------------------
# Executor traceability gate
# ---------------------------------------------------------------------------


def _chat_response(content: dict) -> ChatResponse:
    return ChatResponse(
        content=json.dumps(content, ensure_ascii=False),
        model="stub-model",
        provider="stub",
        request_id="req-1",
        latency_ms=0,
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _valid_output(refs: list[str]) -> dict:
    return {
        "headline": "后端工程师定位",
        "targeted_bullets": [
            {
                "section": "项目经历",
                "bullet": "按JD改写的项目条目",
                "matched_requirement": "Python",
                "source_fact_refs": refs,
            },
        ],
        "matched_requirements": ["Python"],
        "do_not_claim": [],
        "one_page_markdown": "# 简历",
    }


def test_validate_accepts_traceable_targeted_resume() -> None:
    executor = ReadinessExecutor(gateway=object())  # type: ignore[arg-type]
    output = executor.validate(
        _chat_response(_valid_output(["P1", "S1"])),
        "targeted_resume",
        valid_fact_ids=set(enumerate_resume_facts(_FACTS)),
    )
    assert output.targeted_bullets[0].source_fact_refs == ["P1", "S1"]


def test_validate_rejects_unknown_fact_refs() -> None:
    executor = ReadinessExecutor(gateway=object())  # type: ignore[arg-type]
    with pytest.raises(ReadinessValidationError) as exc_info:
        executor.validate(
            _chat_response(_valid_output(["P1", "X9"])),
            "targeted_resume",
            valid_fact_ids=set(enumerate_resume_facts(_FACTS)),
        )
    assert exc_info.value.kind == "traceability"
    assert "X9" in str(exc_info.value)


def test_validate_rejects_targeted_resume_without_facts() -> None:
    executor = ReadinessExecutor(gateway=object())  # type: ignore[arg-type]
    with pytest.raises(ReadinessValidationError) as exc_info:
        executor.validate(
            _chat_response(_valid_output(["P1"])),
            "targeted_resume",
            valid_fact_ids=set(),
        )
    assert exc_info.value.kind == "traceability"


def test_validate_rejects_empty_source_fact_refs_as_schema_error() -> None:
    executor = ReadinessExecutor(gateway=object())  # type: ignore[arg-type]
    with pytest.raises(ReadinessValidationError) as exc_info:
        executor.validate(
            _chat_response(_valid_output([])),
            "targeted_resume",
            valid_fact_ids={"P1"},
        )
    assert exc_info.value.kind == "schema"


# ---------------------------------------------------------------------------
# Worker end-to-end
# ---------------------------------------------------------------------------


def _seed_with_facts(user_id: str, facts: dict | None) -> dict[str, str]:
    """Seed the readiness chain with (optionally) extracted resume facts."""
    parsed_facts: dict = {"_parser": "model", "_parser_status": "succeeded"}
    if facts is not None:
        parsed_facts["facts"] = facts
    with SessionLocal() as db:
        _make_user(db, user_id)
        resume = Resume(user_id=user_id, filename="r.txt")
        db.add(resume)
        db.flush()
        version = ResumeVersion(
            resume_id=resume.id,
            version_no=1,
            raw_text="张三\nPython 5年 FastAPI",
            parsed_facts=parsed_facts,
        )
        db.add(version)
        db.flush()
        job = _make_job(db, user_id)
        app = _make_application(db, user_id, job.id, version.id)
        db.commit()
        return {
            "user_id": user_id,
            "resume_id": resume.id,
            "resume_version_id": version.id,
            "job_id": job.id,
            "application_id": app.id,
        }


def _run_ids(user_id: str, ids: dict[str, str]) -> tuple[str, str]:
    source_hash = _compute_source_hash_for(
        ids["user_id"], ids["job_id"], ids["resume_version_id"]
    )
    run_id = _create_queued_readiness_run(
        user_id,
        ids["application_id"],
        ids["job_id"],
        ids["resume_id"],
        ids["resume_version_id"],
        "targeted_resume",
        source_hash,
    )
    return run_id, source_hash


@pytest.mark.asyncio
async def test_handler_targeted_resume_success_with_facts() -> None:
    """Full chain: fake gateway output traces to seeded facts → artifact saved."""
    ids = _seed_with_facts("tr_ok", _FACTS)
    run_id, source_hash = _run_ids("tr_ok", ids)
    payload = _make_payload(
        run_id,
        "tr_ok",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "targeted_resume",
        source_hash,
    )

    result = await readiness_generation({}, payload)

    assert result == run_id
    valid_ids = set(enumerate_resume_facts(_FACTS))
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "succeeded"

        artifact = (
            db.execute(
                select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id)
            )
            .scalars()
            .first()
        )
        assert artifact is not None
        assert artifact.artifact_type == "targeted_resume"
        assert artifact.prompt_version == "readiness-v1"

        # Every persisted bullet must be traceable to a seeded resume fact.
        content = json.loads(artifact.content)
        assert content["targeted_bullets"]
        assert content["one_page_markdown"]
        for bullet in content["targeted_bullets"]:
            assert bullet["source_fact_refs"]
            for ref in bullet["source_fact_refs"]:
                assert ref in valid_ids


@pytest.mark.asyncio
async def test_handler_targeted_resume_fails_without_facts() -> None:
    """No extracted facts → traceability impossible → non-retryable data failure."""
    ids = _seed_with_facts("tr_nofacts", facts=None)
    run_id, source_hash = _run_ids("tr_nofacts", ids)
    payload = _make_payload(
        run_id,
        "tr_nofacts",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "targeted_resume",
        source_hash,
    )

    await readiness_generation({}, payload)

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        steps = (
            db.execute(select(AgentStep).where(AgentStep.run_id == run_id)).scalars().all()
        )
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "validate_model_output"
        assert failed[0].result["kind"] == "traceability"

        artifacts = (
            db.execute(
                select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id)
            )
            .scalars()
            .all()
        )
        assert artifacts == []

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["code"] == "resume_facts_missing"
        assert app.latest_error["retryable"] is False
        assert app.latest_error["next_action"] == "edit_source"


class _UntraceableStubGateway(ModelGateway):
    """Gateway whose targeted_resume output cites a fabricated fact ref."""

    provider_name = "stub"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=json.dumps(_valid_output(["Z99"]), ensure_ascii=False),
            model="stub-model",
            provider=self.provider_name,
            request_id=request.request_id,
            latency_ms=0,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


@pytest.mark.asyncio
async def test_handler_targeted_resume_fails_on_unresolvable_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model invents a fact ref → retryable validation failure, no artifact."""
    monkeypatch.setattr(
        "app.models_gateway.factory.get_model_gateway",
        lambda: _UntraceableStubGateway(),
    )
    ids = _seed_with_facts("tr_badref", _FACTS)
    run_id, source_hash = _run_ids("tr_badref", ids)
    payload = _make_payload(
        run_id,
        "tr_badref",
        ids["application_id"],
        ids["job_id"],
        ids["resume_version_id"],
        "targeted_resume",
        source_hash,
    )

    await readiness_generation({}, payload)

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == "failed"

        artifacts = (
            db.execute(
                select(GeneratedArtifact).where(GeneratedArtifact.agent_run_id == run_id)
            )
            .scalars()
            .all()
        )
        assert artifacts == []

        app = db.get(ApplicationRecord, ids["application_id"])
        assert app is not None
        assert app.latest_error is not None
        assert app.latest_error["code"] == "invalid_model_output"
        assert app.latest_error["retryable"] is True
        assert app.latest_error["category"] == "validation"
