"""Unit + integration tests for the BOSS recommended-job inspect service.

Covers:

- ``upsert_job_from_browser_jd`` creates a new job with provenance.
- Re-reading the same page (same ``page_url_hash``) returns the existing job.
- Different ``page_url_hash`` creates a separate job.
- Cross-user: same ``page_url_hash`` for different users creates separate jobs.
- ``is_jd_too_sparse`` when title or description is missing.
- Application created and deduped correctly.
- Source changes (updating JD fields on re-read) update the existing job.
- ``inspect_current_job`` returns ``read_failed`` when JD is ``None``.
- ``inspect_current_job`` returns ``jd_too_sparse`` when title missing.

Tests use the shared ``client`` fixture's DB (truncated per test) and direct
Session access for setup/assertions.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.models import Resume, ResumeVersion, UserProfile
from app.db.session import SessionLocal
from app.schemas.boss_recommended_job import InspectStatus
from app.services.boss_recommended_job_service import (
    inspect_current_job,
    is_jd_too_sparse,
    upsert_job_from_browser_jd,
)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_user(db: Session, user_id: str) -> UserProfile:
    user = UserProfile(id=user_id, display_name=f"用户 {user_id}")
    db.add(user)
    db.flush()
    return user


def _make_resume(
    db: Session, user_id: str
) -> tuple[Resume, ResumeVersion]:
    resume = Resume(user_id=user_id, filename="r.txt")
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        version_no=1,
        raw_text="张三\nPython 5年 FastAPI",
        parsed_facts={"_parser": "text", "_parser_status": "parsed"},
    )
    db.add(version)
    db.flush()
    return resume, version


def _make_jd_dict(
    *,
    page_url_hash: str = "sha256:abcdef12",
    title: str | None = "高级前端工程师",
    company: str | None = "字节跳动",
    description: str | None = "负责前端架构设计和性能优化。",
    location: str | None = "北京",
    salary: str | None = "25-50K·15薪",
    skills: list[str] | None = None,
) -> dict:
    if skills is None:
        skills = ["React", "TypeScript"]
    return {
        "title": title,
        "company": company,
        "location": location,
        "salary": salary,
        "experience": "3-5年",
        "education": "本科",
        "skills": skills,
        "description": description,
        "source_kind": "boss_userscript_read_jd",
        "page_url_hash": page_url_hash,
    }


# ---------------------------------------------------------------------------
# is_jd_too_sparse (pure functions — no DB needed, but client truncates anyway)
# ---------------------------------------------------------------------------


def test_is_jd_too_sparse_none(client: TestClient) -> None:
    assert is_jd_too_sparse(None) is True


def test_is_jd_too_sparse_missing_title(client: TestClient) -> None:
    jd = _make_jd_dict(title=None)
    assert is_jd_too_sparse(jd) is True


def test_is_jd_too_sparse_missing_description(client: TestClient) -> None:
    jd = _make_jd_dict(description=None)
    assert is_jd_too_sparse(jd) is True


def test_is_jd_too_sparse_empty_title(client: TestClient) -> None:
    jd = _make_jd_dict(title="  ")
    assert is_jd_too_sparse(jd) is True


def test_is_jd_too_sparse_complete_jd(client: TestClient) -> None:
    jd = _make_jd_dict()
    assert is_jd_too_sparse(jd) is False


# ---------------------------------------------------------------------------
# upsert_job_from_browser_jd
# ---------------------------------------------------------------------------


def test_upsert_creates_new_job_with_provenance(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict()
        job, is_new = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd, agent_run_id="run_123"
        )
        db.commit()
        db.refresh(job)

        assert is_new is True
        assert job.platform == "boss"
        assert job.external_id == "sha256:abcdef12"
        assert job.title == "高级前端工程师"
        assert job.company == "字节跳动"
        assert job.jd_raw is not None
        assert "高级前端工程师" in job.jd_raw

        # Provenance stored in jd_normalized._source.
        norm = job.jd_normalized
        assert norm is not None
        source = norm["_source"]
        assert source["source_kind"] == "boss_userscript_read_jd"
        assert source["page_url_hash"] == "sha256:abcdef12"
        assert source["agent_run_id"] == "run_123"
        assert "read_at" in source

        # Structured fields stored under "fields".
        fields = norm["fields"]
        assert fields["title"] == "高级前端工程师"
        assert fields["company"] == "字节跳动"
        assert fields["skills"] == ["React", "TypeScript"]


def test_upsert_re_read_same_page_returns_existing(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict()
        job1, is_new1 = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd, agent_run_id="run_1"
        )
        db.commit()

        job2, is_new2 = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd, agent_run_id="run_2"
        )
        db.commit()

        assert is_new1 is True
        assert is_new2 is False
        assert job1.id == job2.id


def test_upsert_different_page_hash_creates_separate_job(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd1 = _make_jd_dict(page_url_hash="sha256:aaaa1111", title="前端工程师")
        jd2 = _make_jd_dict(page_url_hash="sha256:bbbb2222", title="后端工程师")
        job1, is_new1 = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd1, agent_run_id="run_1"
        )
        job2, is_new2 = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd2, agent_run_id="run_2"
        )
        db.commit()

        assert is_new1 is True
        assert is_new2 is True
        assert job1.id != job2.id
        assert job1.title == "前端工程师"
        assert job2.title == "后端工程师"


def test_upsert_cross_user_same_hash_creates_separate_jobs(client: TestClient) -> None:
    with SessionLocal() as db:
        user_a = _make_user(db, "user_a")
        user_b = _make_user(db, "user_b")
        db.commit()

        jd = _make_jd_dict(page_url_hash="sha256:shared1")
        job_a, is_new_a = upsert_job_from_browser_jd(
            db, user_id=user_a.id, jd_dict=jd, agent_run_id="run_a"
        )
        job_b, is_new_b = upsert_job_from_browser_jd(
            db, user_id=user_b.id, jd_dict=jd, agent_run_id="run_b"
        )
        db.commit()

        assert is_new_a is True
        assert is_new_b is True
        assert job_a.id != job_b.id
        assert job_a.user_id == user_a.id
        assert job_b.user_id == user_b.id


def test_upsert_updates_existing_job_on_re_read_with_changed_fields(
    client: TestClient,
) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd1 = _make_jd_dict(title="前端工程师", description="旧描述")
        job1, _ = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd1, agent_run_id="run_1"
        )
        db.commit()

        jd2 = _make_jd_dict(title="高级前端工程师", description="新描述")
        job2, is_new2 = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd2, agent_run_id="run_2"
        )
        db.commit()
        db.refresh(job2)

        assert is_new2 is False
        assert job2.id == job1.id
        assert job2.title == "高级前端工程师"
        # jd_normalized updated with new fields.
        assert job2.jd_normalized["fields"]["description"] == "新描述"
        # Provenance updated with new agent_run_id.
        assert job2.jd_normalized["_source"]["agent_run_id"] == "run_2"


def test_upsert_missing_page_url_hash_raises(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict()
        jd["page_url_hash"] = None
        try:
            upsert_job_from_browser_jd(
                db, user_id=user.id, jd_dict=jd, agent_run_id="run_1"
            )
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass


def test_upsert_missing_company_uses_placeholder(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict(company=None)
        job, _ = upsert_job_from_browser_jd(
            db, user_id=user.id, jd_dict=jd, agent_run_id="run_1"
        )
        db.commit()
        db.refresh(job)

        assert job.company == "(未知公司)"


# ---------------------------------------------------------------------------
# inspect_current_job
# ---------------------------------------------------------------------------


def test_inspect_read_failed_when_jd_none(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        result = inspect_current_job(
            db, user, jd_dict=None, agent_run_id="run_1"
        )
        assert result.status == InspectStatus.read_failed
        assert result.job is None
        assert result.application is None


def test_inspect_jd_too_sparse_when_title_missing(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict(title=None)
        result = inspect_current_job(
            db, user, jd_dict=jd, agent_run_id="run_1"
        )
        assert result.status == InspectStatus.jd_too_sparse
        assert result.job is None


def test_inspect_jd_too_sparse_when_description_missing(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict(description="")
        result = inspect_current_job(
            db, user, jd_dict=jd, agent_run_id="run_1"
        )
        assert result.status == InspectStatus.jd_too_sparse
        assert result.job is None


def test_inspect_creates_job_without_application(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict()
        result = inspect_current_job(
            db,
            user,
            jd_dict=jd,
            agent_run_id="run_1",
            resume_version_id=None,
        )

        assert result.status == InspectStatus.ok
        assert result.job is not None
        assert result.application is None
        assert result.is_new_job is True
        assert result.is_new_application is False


def test_inspect_creates_job_and_application(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        _, version = _make_resume(db, user.id)
        db.commit()

        jd = _make_jd_dict()
        result = inspect_current_job(
            db,
            user,
            jd_dict=jd,
            agent_run_id="run_1",
            resume_version_id=version.id,
        )

        assert result.status == InspectStatus.ok
        assert result.job is not None
        assert result.application is not None
        assert result.is_new_job is True
        assert result.is_new_application is True
        assert result.application.job_id == result.job.id
        assert result.application.resume_version_id == version.id
        assert result.application.status == "planned"


def test_inspect_dedupes_application_on_re_read(client: TestClient) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        _, version = _make_resume(db, user.id)
        db.commit()

        jd = _make_jd_dict()
        result1 = inspect_current_job(
            db,
            user,
            jd_dict=jd,
            agent_run_id="run_1",
            resume_version_id=version.id,
        )
        result2 = inspect_current_job(
            db,
            user,
            jd_dict=jd,
            agent_run_id="run_2",
            resume_version_id=version.id,
        )

        assert result1.is_new_job is True
        assert result1.is_new_application is True
        assert result2.is_new_job is False
        assert result2.is_new_application is False
        assert result1.job.id == result2.job.id
        assert result1.application.id == result2.application.id


def test_inspect_resume_version_not_found_returns_404(client: TestClient) -> None:
    from fastapi import HTTPException

    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict()
        try:
            inspect_current_job(
                db,
                user,
                jd_dict=jd,
                agent_run_id="run_1",
                resume_version_id="nonexistent_version",
            )
            raise AssertionError("Expected HTTPException 404")
        except HTTPException as exc:
            assert exc.status_code == 404
            assert "resume version not found" in exc.detail


def test_inspect_missing_page_url_hash_returns_read_failed(
    client: TestClient,
) -> None:
    with SessionLocal() as db:
        user = _make_user(db, "boss_user")
        db.commit()

        jd = _make_jd_dict()
        jd["page_url_hash"] = None
        result = inspect_current_job(
            db, user, jd_dict=jd, agent_run_id="run_1"
        )
        # Missing page_url_hash causes upsert to raise ValueError, which the
        # service catches and returns as read_failed.
        assert result.status == InspectStatus.read_failed
        assert result.job is None
