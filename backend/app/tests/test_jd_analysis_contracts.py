"""Phase 1 contract tests for the resume-aware JD analysis workflow.

Covers:
- structured output schema validation (valid + invalid cases);
- prompt message builder (returns messages, includes the no-invention rule,
  respects resume/JD caps, reports truncation metadata);
- context loader (ownership rejection for job/resume, missing raw text);
- repositories (create/get/list against the test DB).

The model gateway is never invoked here; that belongs to Phase 3+. Tests use
the same truncated test DB fixture as the rest of the suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.prompts.jd_analysis import (
    JD_RAW_CAP,
    PROMPT_VERSION,
    RESUME_RAW_TEXT_CAP,
    JdAnalysisContext,
    build_jd_analysis_messages,
)
from app.db.models.models import (
    JobPosting,
    Resume,
    ResumeVersion,
    UserProfile,
)
from app.db.repositories import (
    agent_run_repo,
    generated_artifact_repo,
    job_analysis_repo,
)
from app.db.session import SessionLocal
from app.schemas.jd_analysis import (
    JdAnalysisEvidence,
    JdAnalysisModelOutput,
    JdAnalysisRiskPoint,
)
from app.services.jd_analysis_service import load_jd_analysis_context

# ---------------------------------------------------------------------------
# Helpers: build rows directly against the DB (bypassing the upload pipeline)
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str = "ctx_user") -> UserProfile:
    user = UserProfile(id=user_id, display_name="测试用户")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session, user_id: str, raw_text: str, filename: str = "r.txt"
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
    db: Session, user_id: str, jd_raw: str = "Python backend, FastAPI, 5y."
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


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


def _valid_output_dict() -> dict[str, Any]:
    return {
        "role_summary": "Senior backend role.",
        "responsibilities": ["Build APIs", "Own services"],
        "hard_requirements": ["Python", "FastAPI"],
        "nice_to_have_requirements": ["Kafka"],
        "resume_match_evidence": [
            {"claim": "Knows Python", "source": "resume", "quote": "Python 5年"},
            {"claim": "JD asks for Python", "source": "jd", "quote": "Python"},
        ],
        "risk_points": [
            {"title": "No Kafka", "detail": "Resume lacks Kafka", "severity": "medium"},
        ],
        "salary_note": "Market range.",
        "growth_note": "Good trajectory.",
        "stability_note": "Stable firm.",
        "match_score": 78,
        "risk_score": 34,
        "skill_gaps": ["Kafka"],
        "interview_preparation": ["Review Kafka basics"],
        "recommendation": "possible_match",
    }


def test_model_output_accepts_valid_payload() -> None:
    out = JdAnalysisModelOutput.model_validate(_valid_output_dict())
    assert out.match_score == 78
    assert out.risk_score == 34
    assert out.recommendation == "possible_match"
    assert out.risk_points[0].severity == "medium"
    assert out.resume_match_evidence[0].source == "resume"


def test_model_output_accepts_null_scores() -> None:
    data = _valid_output_dict()
    data["match_score"] = None
    data["risk_score"] = None
    data["recommendation"] = "not_enough_info"
    out = JdAnalysisModelOutput.model_validate(data)
    assert out.match_score is None
    assert out.risk_score is None


def test_model_output_rejects_score_out_of_range() -> None:
    data = _valid_output_dict()
    data["match_score"] = 101
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_model_output_rejects_negative_score() -> None:
    data = _valid_output_dict()
    data["risk_score"] = -1
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_model_output_rejects_bad_severity() -> None:
    data = _valid_output_dict()
    data["risk_points"][0]["severity"] = "critical"
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_model_output_rejects_bad_evidence_source() -> None:
    data = _valid_output_dict()
    data["resume_match_evidence"][0]["source"] = "internet"
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_model_output_rejects_bad_recommendation() -> None:
    data = _valid_output_dict()
    data["recommendation"] = "definitely_match"
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_model_output_rejects_missing_required_field() -> None:
    data = _valid_output_dict()
    del data["role_summary"]
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_risk_point_and_evidence_submodels() -> None:
    rp = JdAnalysisRiskPoint(title="x", detail="y", severity="high")
    assert rp.severity == "high"
    ev = JdAnalysisEvidence(claim="c", source="profile")
    assert ev.quote is None  # quote is optional


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _make_context(
    *,
    resume_text: str = "张三\nPython 5年",
    jd_text: str = "Hire a Python backend engineer.",
) -> JdAnalysisContext:
    return JdAnalysisContext(
        user_id="ctx_user",
        profile={
            "display_name": "测试用户",
            "career_direction": "engineering",
            "base_location": "北京",
            "preferred_locations": ["北京"],
            "salary_min": 20000,
            "salary_max": 40000,
            "strengths": ["Python"],
            "constraints": {"remote": True},
        },
        job={
            "id": "job_1",
            "company": "Acme",
            "title": "Backend Engineer",
            "location": "北京",
            "salary_range": "20-40k",
            "direction": "engineering",
            "jd_raw": jd_text,
        },
        resume={
            "resume_id": "res_1",
            "resume_version_id": "ver_1",
            "filename": "r.txt",
            "parser_status": "parsed",
            "parser_name": "text",
            "raw_text": resume_text,
            "parsed_facts": {"_parser": "text", "_parser_status": "parsed"},
        },
    )


def test_prompt_version_constant() -> None:
    assert PROMPT_VERSION == "jd-analysis-v1"


def test_build_messages_returns_system_and_user() -> None:
    result = build_jd_analysis_messages(_make_context())
    assert len(result.messages) == 2
    assert result.messages[0].role == "system"
    assert result.messages[1].role == "user"


def test_build_messages_includes_no_invention_rule() -> None:
    result = build_jd_analysis_messages(_make_context())
    system = result.messages[0].content
    # The no-invention rule must be present in the system message.
    assert "NEVER invent resume facts" in system


def test_build_messages_includes_all_source_sections() -> None:
    result = build_jd_analysis_messages(_make_context())
    user = result.messages[1].content
    assert "## USER PROFILE" in user
    assert "## RESUME" in user
    assert "## JOB DESCRIPTION" in user
    assert "## REQUIRED OUTPUT" in user
    assert "张三" in user
    assert "Python backend engineer" in user


def test_build_messages_includes_schema_in_user_message() -> None:
    result = build_jd_analysis_messages(_make_context())
    user = result.messages[1].content
    assert "role_summary" in user
    assert "match_score" in user
    assert "recommendation" in user


def test_build_messages_resumes_resume_cap() -> None:
    big_resume = "R" * (RESUME_RAW_TEXT_CAP + 500)
    result = build_jd_analysis_messages(
        _make_context(resume_text=big_resume), resume_cap=RESUME_RAW_TEXT_CAP
    )
    assert result.truncation["resume_raw_text_total_chars"] == len(big_resume)
    assert result.truncation["resume_raw_text_dropped_chars"] == 500
    # The user message must contain the capped text, not the full text.
    assert ("R" * RESUME_RAW_TEXT_CAP) in result.messages[1].content
    assert len(big_resume) not in [
        len(result.messages[1].content),
    ]


def test_build_messages_resumes_jd_cap() -> None:
    big_jd = "J" * (JD_RAW_CAP + 300)
    result = build_jd_analysis_messages(_make_context(jd_text=big_jd), jd_cap=JD_RAW_CAP)
    assert result.truncation["jd_raw_total_chars"] == len(big_jd)
    assert result.truncation["jd_raw_dropped_chars"] == 300


def test_build_messages_no_truncation_when_under_cap() -> None:
    result = build_jd_analysis_messages(_make_context())
    assert result.truncation["resume_raw_text_dropped_chars"] == 0
    assert result.truncation["jd_raw_dropped_chars"] == 0


def test_build_messages_custom_caps_override_defaults() -> None:
    result = build_jd_analysis_messages(_make_context(resume_text="1234567890"), resume_cap=4)
    assert result.truncation["resume_raw_text_dropped_chars"] == 6
    assert "1234" in result.messages[1].content


# ---------------------------------------------------------------------------
# Context loader: ownership and data quality
# ---------------------------------------------------------------------------


def _load_context(
    db: Session,
    user: UserProfile,
    job_id: str,
    resume_version_id: str,
) -> JdAnalysisContext:
    return load_jd_analysis_context(db, user, job_id, resume_version_id)


def test_load_context_success(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "load_ok")
        _, version = _make_resume(db, "load_ok", "Python 5年 FastAPI")
        job = _make_job(db, "load_ok")
        db.commit()

    with SessionLocal() as db:
        fresh_user = db.get(UserProfile, "load_ok")
        assert fresh_user is not None
        ctx = _load_context(db, fresh_user, job.id, version.id)
        assert ctx.user_id == "load_ok"
        assert ctx.job["id"] == job.id
        assert ctx.resume["resume_version_id"] == version.id
        assert ctx.resume["raw_text"] == "Python 5年 FastAPI"
        assert ctx.profile["display_name"] == "测试用户"


def test_load_context_rejects_missing_job(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "no_job")
        _, version = _make_resume(db, "no_job", "text")
        db.commit()

    with SessionLocal() as db:
        fresh_user = db.get(UserProfile, "no_job")
        assert fresh_user is not None
        with pytest.raises(HTTPException) as exc:
            _load_context(db, fresh_user, "nonexistent_job", version.id)
        assert exc.value.status_code == 404
        assert exc.value.detail == "job not found"


def test_load_context_rejects_cross_user_job(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "owner_a")
        _make_user(db, "owner_b")
        _, version_other = _make_resume(db, "owner_b", "text")
        job_owner = _make_job(db, "owner_a")
        db.commit()

    with SessionLocal() as db:
        intruder = db.get(UserProfile, "owner_b")
        assert intruder is not None
        with pytest.raises(HTTPException) as exc:
            _load_context(db, intruder, job_owner.id, version_other.id)
        assert exc.value.status_code == 404
        assert exc.value.detail == "job not found"


def test_load_context_rejects_missing_resume_version(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "no_ver")
        job = _make_job(db, "no_ver")
        db.commit()

    with SessionLocal() as db:
        fresh_user = db.get(UserProfile, "no_ver")
        assert fresh_user is not None
        with pytest.raises(HTTPException) as exc:
            _load_context(db, fresh_user, job.id, "nonexistent_version")
        assert exc.value.status_code == 404
        assert exc.value.detail == "resume version not found"


def test_load_context_rejects_cross_user_resume_version(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "resume_owner")
        _make_user(db, "resume_intruder")
        _, version = _make_resume(db, "resume_owner", "secret text")
        job = _make_job(db, "resume_intruder")
        db.commit()

    with SessionLocal() as db:
        fresh_intruder = db.get(UserProfile, "resume_intruder")
        assert fresh_intruder is not None
        with pytest.raises(HTTPException) as exc:
            _load_context(db, fresh_intruder, job.id, version.id)
        assert exc.value.status_code == 404
        assert exc.value.detail == "resume version not found"


def test_load_context_rejects_empty_raw_text(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "empty_text")
        # Empty string raw_text (e.g. unsupported parser result).
        _, version = _make_resume(db, "empty_text", "")
        job = _make_job(db, "empty_text")
        db.commit()

    with SessionLocal() as db:
        fresh_user = db.get(UserProfile, "empty_text")
        assert fresh_user is not None
        with pytest.raises(HTTPException) as exc:
            _load_context(db, fresh_user, job.id, version.id)
        assert exc.value.status_code == 422
        assert exc.value.detail == "resume version has no parsed text"


def test_load_context_rejects_blank_raw_text(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "blank_text")
        _, version = _make_resume(db, "blank_text", "   \n\t  ")
        job = _make_job(db, "blank_text")
        db.commit()

    with SessionLocal() as db:
        fresh_user = db.get(UserProfile, "blank_text")
        assert fresh_user is not None
        with pytest.raises(HTTPException) as exc:
            _load_context(db, fresh_user, job.id, version.id)
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Repositories: create / get / list
# ---------------------------------------------------------------------------


def test_job_analysis_repo_create_get_list(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "ja_repo")
        job = _make_job(db, "ja_repo")
        analysis = job_analysis_repo.create(
            db,
            job_id=job.id,
            agent_run_id=None,
            match_score=80.0,
            risk_score=20.0,
            summary="OK",
            salary_analysis={"note": "good"},
            growth_analysis={"note": "up"},
            stability_analysis={"note": "stable"},
        )
        db.commit()
        aid = analysis.id

    with SessionLocal() as db:
        got = job_analysis_repo.get(db, aid)
        assert got is not None
        assert got.match_score == 80.0
        assert got.salary_analysis == {"note": "good"}

        rows, total = job_analysis_repo.list_for_job(db, job.id, page=1, page_size=10)
        assert total == 1
        assert rows[0].id == aid


def test_generated_artifact_repo_create_get_list(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "ga_repo")
        job = _make_job(db, "ga_repo")
        _, version = _make_resume(db, "ga_repo", "text")
        artifact = generated_artifact_repo.create(
            db,
            artifact_type="jd_analysis",
            content='{"role_summary":"x"}',
            user_id="ga_repo",
            job_id=job.id,
            resume_version_id=version.id,
            agent_run_id=None,
            source_ids={"job_id": job.id, "resume_version_id": version.id},
            prompt_version=PROMPT_VERSION,
            model_name="fake-model",
        )
        db.commit()
        aid = artifact.id

    with SessionLocal() as db:
        got = generated_artifact_repo.get(db, aid)
        assert got is not None
        assert got.artifact_type == "jd_analysis"
        assert got.prompt_version == PROMPT_VERSION
        assert got.source_ids["job_id"] == job.id

        rows, total = generated_artifact_repo.list_for_job(
            db, job.id, artifact_type="jd_analysis", page=1, page_size=10
        )
        assert total == 1
        assert rows[0].id == aid

        # Filter by a different type returns nothing.
        _, other_total = generated_artifact_repo.list_for_job(
            db, job.id, artifact_type="hr_opening_message", page=1, page_size=10
        )
        assert other_total == 0


def test_agent_run_repo_create_update_step_list(client: TestClient) -> None:
    with SessionLocal() as db:
        _make_user(db, "ar_repo")
        run = agent_run_repo.create_run(
            db,
            user_id="ar_repo",
            workflow_type="resume_aware_jd_analysis",
            status="running",
        )
        agent_run_repo.add_step(
            db,
            run_id=run.id,
            step_no=1,
            name="load_context",
            status="succeeded",
            result={"ok": True},
        )
        db.commit()
        run_id = run.id

    with SessionLocal() as db:
        got = agent_run_repo.get_run(db, run_id)
        assert got is not None
        assert got.status == "running"
        assert got.workflow_type == "resume_aware_jd_analysis"

        # Update status and add a second step.
        agent_run_repo.update_status(
            db,
            got,
            status="succeeded",
            result={"analysis_id": "a1"},
        )
        agent_run_repo.add_step(
            db,
            run_id=run_id,
            step_no=2,
            name="persist_outputs",
            status="succeeded",
        )
        db.commit()

    with SessionLocal() as db:
        got = agent_run_repo.get_run(db, run_id)
        assert got is not None
        assert got.status == "succeeded"
        assert got.result == {"analysis_id": "a1"}
        steps = agent_run_repo.list_steps(db, run_id)
        assert [s.step_no for s in steps] == [1, 2]
        assert steps[0].name == "load_context"

        rows, total = agent_run_repo.list_runs_for_user(
            db, "ar_repo", workflow_type="resume_aware_jd_analysis", page=1, page_size=10
        )
        assert total == 1
        assert rows[0].id == run_id

        # Other user sees nothing.
        _, other_total = agent_run_repo.list_runs_for_user(db, "someone_else", page=1, page_size=10)
        assert other_total == 0
