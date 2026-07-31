"""Tests for the resume fact extraction service orchestration.

Covers:
- success path (6 ordered step names, sanitized results, _extraction.status
  succeeded, facts present, SUPER_SECRET_RESUME_TOKEN never in persisted rows);
- model-invalid output (failed run persisted);
- gateway-error (failed run persisted);
- sparse resume (valid object + uncertain_fields);
- unsupported format / empty raw_text (run not created, status not_run);
- extraction status block shape and telemetry preservation.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.models import AgentRun, Resume, ResumeVersion, UserProfile
from app.db.repositories import agent_run_repo
from app.db.session import SessionLocal
from app.models_gateway.base import (
    ChatRequest,
    ChatResponse,
    ModelGateway,
)
from app.models_gateway.fake import FakeModelGateway
from app.services import resume_fact_service

SECRET = "SUPER_SECRET_RESUME_TOKEN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resume(
    db: Session,
    user_id: str = "rf_user",
    raw_text: str = f"name: {SECRET}\nPython 5年 FastAPI",
    filename: str = "r.txt",
    parser_status: str = "parsed",
) -> tuple[Resume, ResumeVersion]:
    user = UserProfile(id=user_id, display_name="测试用户")
    db.add(user)
    db.flush()
    resume = Resume(user_id=user_id, filename=filename)
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        version_no=1,
        raw_text=raw_text,
        parsed_facts={"_parser": "text", "_parser_status": parser_status},
    )
    db.add(version)
    db.flush()
    return resume, version


class _InvalidJsonGateway(ModelGateway):
    """Returns non-JSON content to trigger ResumeFactValidationError(kind=json)."""

    provider_name = "fake"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="not json at all",
            model="fake-model",
            provider=self.provider_name,
            request_id="req_invalid",
            latency_ms=1,
            usage=None,
        )


class _SchemaInvalidGateway(ModelGateway):
    """Returns JSON that fails ResumeFactsModelOutput schema validation."""

    provider_name = "fake"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=json.dumps({"years_of_experience": "not-an-int"}),
            model="fake-model",
            provider=self.provider_name,
            request_id="req_schema",
            latency_ms=1,
            usage=None,
        )


class _ErrorGateway(ModelGateway):
    """Raises on chat to simulate a provider/gateway error."""

    provider_name = "fake"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("provider unavailable")


class _SparseGateway(ModelGateway):
    """Returns a sparse facts object with uncertain_fields populated."""

    provider_name = "fake"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=json.dumps(
                {
                    "uncertain_fields": [
                        {"field": "contact.email", "reason": "not found in resume text"},
                    ],
                }
            ),
            model="fake-model",
            provider=self.provider_name,
            request_id="req_sparse",
            latency_ms=1,
            usage=None,
        )


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_success_writes_facts_and_six_ordered_steps(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_ok")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        assert resume is not None and version is not None
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, FakeModelGateway()
        )
        assert outcome.status == "succeeded"
        assert outcome.run is not None
        run_id = outcome.run.id

    with SessionLocal() as db:
        steps = agent_run_repo.list_steps(db, run_id)
        assert [s.name for s in steps] == [
            "load_context",
            "build_prompt_context",
            "call_model",
            "validate_model_output",
            "persist_outputs",
            "complete_run",
        ]
        assert all(s.status == "succeeded" for s in steps)

        # Sanitization: no step result contains the secret resume token.
        for s in steps:
            blob = json.dumps(s.result or {}, ensure_ascii=False)
            assert SECRET not in blob

        version = db.get(ResumeVersion, version_id)
        assert version is not None
        facts = version.parsed_facts or {}
        assert facts["_parser"] == "text"
        assert facts["_parser_status"] == "parsed"
        assert facts["_extraction"]["status"] == "succeeded"
        assert facts["_extraction"]["run_id"] == run_id
        assert facts["_extraction"]["prompt_version"] == "resume-fact-extraction-v1"
        assert facts["facts"]["contact"]["name"] == "张三"
        assert "Python" in facts["facts"]["skills"]


@pytest.mark.asyncio
async def test_extract_success_run_status_succeeded(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_run")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, FakeModelGateway()
        )
        run = outcome.run
        assert run is not None
        run_id = run.id

    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.workflow_type == "resume_fact_extraction"
        # Run result must not leak the secret token.
        blob = json.dumps(run.result or {}, ensure_ascii=False)
        assert SECRET not in blob


# ---------------------------------------------------------------------------
# Model-invalid failure (JSON + schema)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_invalid_json_persists_failed_run_no_raise(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_json")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, _InvalidJsonGateway()
        )
        assert outcome.status == "failed"
        run_id = outcome.run.id if outcome.run else None
        assert run_id is not None

    with SessionLocal() as db:
        run = agent_run_repo.get_run(db, run_id)
        assert run is not None
        assert run.status == "failed"
        steps = agent_run_repo.list_steps(db, run_id)
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "validate_model_output"

        version = db.get(ResumeVersion, version_id)
        assert version is not None
        assert version.parsed_facts["_extraction"]["status"] == "failed"
        # No facts key written on failure.
        assert "facts" not in (version.parsed_facts or {})


@pytest.mark.asyncio
async def test_extract_schema_invalid_persists_failed_run(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_schema")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, _SchemaInvalidGateway()
        )
        assert outcome.status == "failed"


# ---------------------------------------------------------------------------
# Gateway error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_gateway_error_persists_failed_run(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_gw")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, _ErrorGateway()
        )
        assert outcome.status == "failed"
        run_id = outcome.run.id

    with SessionLocal() as db:
        steps = agent_run_repo.list_steps(db, run_id)
        failed = [s for s in steps if s.status == "failed"]
        assert len(failed) == 1
        assert failed[0].name == "call_model"


# ---------------------------------------------------------------------------
# Sparse resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_sparse_resume_produces_valid_facts(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_sparse", raw_text="just a name")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, _SparseGateway()
        )
        assert outcome.status == "succeeded"

    with SessionLocal() as db:
        version = db.get(ResumeVersion, version_id)
        assert version is not None
        facts = version.parsed_facts["facts"]
        assert facts["uncertain_fields"] == [
            {"field": "contact.email", "reason": "not found in resume text"}
        ]


# ---------------------------------------------------------------------------
# Unsupported / empty raw_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_empty_raw_text_no_run_status_not_run(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_empty", raw_text="", parser_status="unsupported")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, FakeModelGateway()
        )
        assert outcome.status == "not_run"
        assert outcome.run is None

    with SessionLocal() as db:
        # No run created.
        rows = db.execute(select(AgentRun).where(AgentRun.user_id == "rf_empty")).scalars().all()
        assert len(rows) == 0

        version = db.get(ResumeVersion, version_id)
        assert version is not None
        assert version.parsed_facts["_extraction"]["status"] == "not_run"
        assert "facts" not in (version.parsed_facts or {})


@pytest.mark.asyncio
async def test_extract_blank_raw_text_treated_as_not_run(client) -> None:  # type: ignore[no-untyped-def]
    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_blank", raw_text="   \n\t  ")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        outcome = await resume_fact_service.extract_resume_facts(
            db, resume, version, FakeModelGateway()
        )
        assert outcome.status == "not_run"


# ---------------------------------------------------------------------------
# raise_on_failure (re-extract contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_raise_on_failure_raises_502(client) -> None:  # type: ignore[no-untyped-def]
    from fastapi import HTTPException

    with SessionLocal() as db:
        resume, version = _make_resume(db, "rf_raise")
        db.commit()
        resume_id, version_id = resume.id, version.id

    with SessionLocal() as db:
        resume = db.get(Resume, resume_id)
        version = db.get(ResumeVersion, version_id)
        with pytest.raises(HTTPException) as exc:
            await resume_fact_service.extract_resume_facts(
                db, resume, version, _InvalidJsonGateway(), raise_on_failure=True
            )
        assert exc.value.status_code == 502
