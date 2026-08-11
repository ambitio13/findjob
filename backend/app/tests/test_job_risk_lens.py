"""Tests for the Phase 3 job-risk lens (岗位风险透视).

Covers:

- Schema contract: ``red_flags`` / ``salary_structure`` / ``stability_signals``
  validate, reject bad flag types and oversized evidence quotes, and keep
  pre-extension artifacts valid (all new fields optional).
- Worker E2E: the fake gateway's risk-lens fields are persisted — red-flag
  summary sanitized (no quotes) on ``JobAnalysis.red_flags``, structure merged
  into ``salary_analysis``, quotes stripped from ``stability_analysis``.
- Job list API: rows carry red-flag labels; ``has_red_flags`` filters with
  correct pagination totals.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models.models import JobAnalysis, JobPosting, UserProfile
from app.db.repositories import job_analysis_repo
from app.db.session import SessionLocal
from app.queue.handlers import resume_aware_jd_analysis
from app.schemas.jd_analysis import (
    JdAnalysisModelOutput,
    JdRedFlag,
    JdSalaryStructure,
    JdStabilitySignal,
)
from app.tests.test_jd_analysis_api import (
    _create_queued_analysis_run,
    _make_payload,
    _seed,
)


def _base_output_dict() -> dict:
    """Minimal schema-valid pre-extension payload."""
    return {
        "role_summary": "Backend role.",
        "responsibilities": ["Build APIs"],
        "hard_requirements": ["Python"],
        "nice_to_have_requirements": [],
        "resume_match_evidence": [],
        "risk_points": [],
        "salary_note": "Market range.",
        "growth_note": "Good.",
        "stability_note": "Stable.",
        "match_score": 80,
        "risk_score": 20,
        "skill_gaps": [],
        "interview_preparation": [],
        "recommendation": "possible_match",
    }


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------


def test_model_output_accepts_risk_lens_fields() -> None:
    data = _base_output_dict()
    data["salary_structure"] = {
        "range_text": "25k-40k",
        "min_value": 25.0,
        "max_value": 40.0,
        "period": "monthly",
        "composition": ["底薪"],
        "caveats": [],
    }
    data["red_flags"] = [
        {
            "flag_type": "training_fee",
            "title": "岗前培训费",
            "detail": "要求先交培训费。",
            "severity": "high",
            "evidence_quote": "入职前需缴纳岗前培训费3000元",
        }
    ]
    data["stability_signals"] = [
        {"polarity": "negative", "signal": "外包驻场", "evidence_quote": "驻场开发"}
    ]
    out = JdAnalysisModelOutput.model_validate(data)
    assert out.red_flags[0].flag_type == "training_fee"
    assert out.salary_structure is not None
    assert out.salary_structure.min_value == 25.0
    assert out.stability_signals[0].polarity == "negative"


def test_model_output_risk_lens_fields_default_empty() -> None:
    """Pre-extension artifacts (no new fields) must still validate."""
    out = JdAnalysisModelOutput.model_validate(_base_output_dict())
    assert out.red_flags == []
    assert out.salary_structure is None
    assert out.stability_signals == []


def test_model_output_rejects_unknown_flag_type() -> None:
    data = _base_output_dict()
    data["red_flags"] = [
        {
            "flag_type": "not_a_real_flag",
            "title": "x",
            "detail": "y",
            "severity": "low",
        }
    ]
    with pytest.raises(ValidationError):
        JdAnalysisModelOutput.model_validate(data)


def test_red_flag_rejects_oversized_evidence_quote() -> None:
    with pytest.raises(ValidationError):
        JdRedFlag(
            flag_type="other",
            title="x",
            detail="y",
            severity="low",
            evidence_quote="字" * 301,
        )


def test_salary_structure_rejects_negative_values() -> None:
    with pytest.raises(ValidationError):
        JdSalaryStructure(min_value=-1.0)


def test_stability_signal_rejects_bad_polarity() -> None:
    with pytest.raises(ValidationError):
        JdStabilitySignal(polarity="great", signal="x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Worker E2E: sanitized persistence of the risk lens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_persists_sanitized_red_flag_summary() -> None:
    """Fake gateway risk-lens output lands on JobAnalysis without quotes."""
    ids = _seed("ja_risk_ok")
    run_id = _create_queued_analysis_run("ja_risk_ok", ids["job_id"], ids["resume_version_id"])
    payload = _make_payload(run_id, "ja_risk_ok", ids["job_id"], ids["resume_version_id"])

    result = await resume_aware_jd_analysis({}, payload)

    assert result == run_id
    with SessionLocal() as db:
        analysis = (
            db.execute(select(JobAnalysis).where(JobAnalysis.agent_run_id == run_id))
            .scalars()
            .first()
        )
        assert analysis is not None

        # Red-flag summary: type/title/severity only, never evidence quotes.
        assert analysis.red_flags == [
            {
                "flag_type": "inflated_salary",
                "title": "薪资区间过宽",
                "severity": "low",
            }
        ]

        # Salary structure merged into salary_analysis.
        assert analysis.salary_analysis is not None
        assert analysis.salary_analysis["note"]
        structure = analysis.salary_analysis["structure"]
        assert structure["range_text"] == "25k-40k"
        assert structure["min_value"] == 25.0

        # Stability signals stored as conclusions only (quotes stripped).
        assert analysis.stability_analysis is not None
        signals = analysis.stability_analysis["signals"]
        assert signals == [
            {"polarity": "unknown", "signal": "JD 未描述团队规模与汇报线。"}
        ]


# ---------------------------------------------------------------------------
# Job list API: risk labels + filter
# ---------------------------------------------------------------------------


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _seed_jobs_with_analyses(user_id: str) -> dict[str, str]:
    """Seed two jobs: one flagged by its newest analysis, one clean."""
    with SessionLocal() as db:
        db.add(UserProfile(id=user_id, display_name=f"用户 {user_id}"))
        db.flush()
        flagged_job = JobPosting(
            user_id=user_id, company="Acme", title="后端工程师", jd_raw="JD A"
        )
        clean_job = JobPosting(
            user_id=user_id, company="Beta", title="前端工程师", jd_raw="JD B"
        )
        db.add_all([flagged_job, clean_job])
        db.flush()
        job_analysis_repo.create(
            db,
            flagged_job.id,
            summary="flagged",
            red_flags=[
                {"flag_type": "training_loan", "title": "培训贷话术", "severity": "high"}
            ],
        )
        job_analysis_repo.create(db, clean_job.id, summary="clean", red_flags=None)
        db.commit()
        return {"flagged_job_id": flagged_job.id, "clean_job_id": clean_job.id}


def test_job_list_carries_red_flag_labels(client: TestClient) -> None:
    ids = _seed_jobs_with_analyses("ja_risk_list")
    resp = client.get("/api/v1/jobs", headers=_headers("ja_risk_list"))
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()["items"]}
    assert items[ids["flagged_job_id"]]["red_flags"] == [
        {"flag_type": "training_loan", "title": "培训贷话术", "severity": "high"}
    ]
    assert items[ids["clean_job_id"]]["red_flags"] == []


def test_job_list_filter_has_red_flags_true(client: TestClient) -> None:
    ids = _seed_jobs_with_analyses("ja_risk_true")
    resp = client.get(
        "/api/v1/jobs", params={"has_red_flags": True}, headers=_headers("ja_risk_true")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert [i["id"] for i in body["items"]] == [ids["flagged_job_id"]]


def test_job_list_filter_has_red_flags_false(client: TestClient) -> None:
    ids = _seed_jobs_with_analyses("ja_risk_false")
    resp = client.get(
        "/api/v1/jobs", params={"has_red_flags": False}, headers=_headers("ja_risk_false")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert [i["id"] for i in body["items"]] == [ids["clean_job_id"]]


def test_latest_red_flags_prefers_newest_analysis() -> None:
    """A re-analysis without flags must clear the label (newest wins)."""
    with SessionLocal() as db:
        db.add(UserProfile(id="ja_risk_newest", display_name="x"))
        db.flush()
        job = JobPosting(
            user_id="ja_risk_newest", company="Acme", title="后端", jd_raw="JD"
        )
        db.add(job)
        db.flush()
        job_analysis_repo.create(
            db,
            job.id,
            red_flags=[{"flag_type": "other", "title": "旧红旗", "severity": "low"}],
        )
        db.flush()
        # Force a strictly later created_at on the newest row.
        from datetime import UTC, datetime, timedelta

        newer = job_analysis_repo.create(db, job.id, red_flags=None)
        newer.created_at = datetime.now(UTC) + timedelta(seconds=5)
        db.commit()

        latest = job_analysis_repo.latest_red_flags_by_job(db, "ja_risk_newest", [job.id])
        assert latest[job.id] == []
