#!/usr/bin/env python3
"""Idempotent demo data seeder.

Creates a demo user with pre-built jobs, a sample resume, match decisions,
application records, and funnel metrics so the first login shows a populated
environment.

Usage (from the backend/ directory or with PYTHONPATH=backend):

    python scripts/seed_demo.py

The script is idempotent: running it twice produces the same state as once
(existing demo data is detected and skipped/updated).

Safety: refuses to run when ``APP_ENV=prod`` unless ``--i-know-this-is-demo``
is passed. The demo user is registered through the normal auth_service path
when possible (requires ``AUTH_SECRET_KEY``); otherwise falls back to creating
the UserProfile directly.
"""

from __future__ import annotations

import argparse
import json

# --- Bootstrap: ensure app is importable when run as a standalone script. ---
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import get_settings  # noqa: E402
from app.db.models.models import (  # noqa: E402
    ApplicationRecord,
    GeneratedArtifact,
    JobPosting,
    Resume,
    ResumeVersion,
)
from app.db.repositories import (  # noqa: E402
    agent_run_repo,
    application_action_repo,
    application_outcome_repo,
    application_repo,
    auth_user_repo,
    generated_artifact_repo,
    job_analysis_repo,
    job_repo,
    resume_repo,
    user_profile_repo,
)
from app.db.session import SessionLocal  # noqa: E402
from app.schemas.application import ApplicationStatus  # noqa: E402
from app.schemas.boss_match_decision import MatchDecision  # noqa: E402
from app.services import auth_service  # noqa: E402

# ---------------------------------------------------------------------------
# Constants — demo content
# ---------------------------------------------------------------------------

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo12345"  # Intentionally public — demo-only credential.
DEMO_DISPLAY_NAME = "演示用户"

PRESET_JOBS: list[dict] = [
    {
        "company": "字节跳动",
        "title": "高级前端工程师",
        "platform": "boss",
        "location": "北京",
        "salary_range": "30-60K·15薪",
        "direction": "前端",
        "jd_raw": (
            "【岗位职责】\n"
            "1. 负责公司核心产品的前端架构设计与开发；\n"
            "2. 参与技术选型，推动前端工程化与性能优化；\n"
            "3. 与产品、设计、后端紧密协作，交付高质量用户体验。\n\n"
            "【任职要求】\n"
            "1. 本科及以上学历，计算机相关专业；\n"
            "2. 5年以上前端开发经验，精通 React/Vue；\n"
            "3. 熟悉 TypeScript、Webpack/Vite、Node.js；\n"
            "4. 有大型 SPA 应用架构经验者优先。"
        ),
        "match_score": 0.88,
        "match_decision": MatchDecision.communicate.value,
        "match_reasons": "5年React经验高度匹配，TypeScript和Vite均为日常使用技术栈。",
    },
    {
        "company": "阿里巴巴",
        "title": "全栈开发工程师",
        "platform": "boss",
        "location": "杭州",
        "salary_range": "25-45K·16薪",
        "direction": "全栈",
        "jd_raw": (
            "【岗位职责】\n"
            "1. 参与电商核心交易链路的全栈开发；\n"
            "2. 负责前后端联调与接口设计；\n"
            "3. 参与系统稳定性建设与性能调优。\n\n"
            "【任职要求】\n"
            "1. 本科及以上学历，3年以上开发经验；\n"
            "2. 熟悉 Java/Spring Boot 后端优先；\n"
            "3. 熟悉 React/Vue 前端框架；\n"
            "4. 了解 MySQL、Redis、消息队列。"
        ),
        "match_score": 0.65,
        "match_decision": MatchDecision.communicate.value,
        "match_reasons": "前端能力匹配，Java后端经验为加分项但非核心要求。",
    },
    {
        "company": "美团",
        "title": "后端开发工程师（Java方向）",
        "platform": "boss",
        "location": "北京",
        "salary_range": "25-40K·14薪",
        "direction": "后端",
        "jd_raw": (
            "【岗位职责】\n"
            "1. 负责到店业务后端服务设计与开发；\n"
            "2. 参与微服务架构演进与治理；\n"
            "3. 负责高并发场景下的系统优化。\n\n"
            "【任职要求】\n"
            "1. 本科及以上学历，计算机相关专业；\n"
            "2. 3年以上Java后端开发经验，精通Spring生态；\n"
            "3. 熟悉MySQL、Redis、Kafka；\n"
            "4. 有分布式系统设计经验。"
        ),
        "match_score": 0.32,
        "match_decision": MatchDecision.skip.value,
        "match_reasons": "岗位以Java后端为核心，候选人前端背景匹配度较低。",
    },
]

SAMPLE_RESUME_TEXT = (
    "张三 | 高级前端工程师\n"
    "联系方式：zhangsan@example.com | 138-0000-0000\n\n"
    "教育背景\n"
    "2015-2019 某大学 计算机科学与技术 本科\n\n"
    "工作经历\n"
    "2021-至今 ABC科技 高级前端工程师\n"
    "- 负责公司核心SaaS产品前端架构，使用React+TypeScript+Vite\n"
    "- 主导前端微服务化改造，首屏加载性能提升40%\n"
    "- 建设前端组件库与工程化体系，覆盖30+业务页面\n\n"
    "2019-2021 XYZ互联网 前端工程师\n"
    "- 使用Vue.js开发营销活动平台，服务千万级日活\n"
    "- 参与Node.js BFF层建设，优化接口聚合策略\n\n"
    "技能\n"
    "- 前端：React, Vue, TypeScript, Webpack, Vite\n"
    "- 后端：Node.js, Express, 基础Java\n"
    "- 工具：Git, Docker, CI/CD"
)

SAMPLE_RESUME_FACTS = {
    "_parser": "text",
    "_parser_status": "ok",
    "name": "张三",
    "title": "高级前端工程师",
    "contact": "zhangsan@example.com",
    "education": "2015-2019 某大学 计算机科学与技术 本科",
    "experience": [
        "2021-至今 ABC科技 高级前端工程师",
        "2019-2021 XYZ互联网 前端工程师",
    ],
    "skills": ["React", "Vue", "TypeScript", "Webpack", "Vite", "Node.js"],
}


def _guard(env: str, *, force: bool) -> None:
    """Refuse to run in prod without the explicit override flag."""
    if env == "prod" and not force:
        sys.exit(
            "ERROR: APP_ENV=prod — refusing to seed demo data without "
            "--i-know-this-is-demo. This script is for demo/test environments only."
        )


def _ensure_demo_user(db) -> str:
    """Register or locate the demo user, return its user_id."""
    existing = auth_user_repo.get_by_username(db, DEMO_USERNAME)
    if existing is not None:
        return existing.id

    try:
        user = auth_service.register(
            db,
            username=DEMO_USERNAME,
            password=DEMO_PASSWORD,
            display_name=DEMO_DISPLAY_NAME,
            invite_code=None,
        )
        return user.id
    except auth_service.AuthError:
        # auth_not_configured (no AUTH_SECRET_KEY) — fall back to a bare profile.
        settings = get_settings()
        user_id = settings.demo_user_id or "demo_user"
        user_profile_repo.ensure_default(db, user_id)
        db.commit()
        return user_id


def _ensure_jobs(db, user_id: str) -> list[tuple[str, dict]]:
    """Create preset jobs (idempotent by company+title). Return [(job_id, preset)]."""
    result: list[tuple[str, dict]] = []
    for preset in PRESET_JOBS:
        existing = (
            db.query(JobPosting)
            .filter(
                JobPosting.user_id == user_id,
                JobPosting.company == preset["company"],
                JobPosting.title == preset["title"],
            )
            .first()
        )
        if existing is not None:
            result.append((existing.id, preset))
            continue

        job = job_repo.create(
            db,
            user_id=user_id,
            company=preset["company"],
            title=preset["title"],
            jd_raw=preset["jd_raw"],
            platform=preset["platform"],
            location=preset["location"],
            salary_range=preset["salary_range"],
            direction=preset["direction"],
        )
        db.flush()
        result.append((job.id, preset))
    return result


def _ensure_resume(db, user_id: str) -> str | None:
    """Create a sample resume with one version. Return resume_version_id or None."""
    existing = (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .first()
    )
    if existing is not None:
        latest_version = (
            db.query(ResumeVersion)
            .filter(ResumeVersion.resume_id == existing.id)
            .order_by(ResumeVersion.version_no.desc())
            .first()
        )
        return latest_version.id if latest_version else None

    resume = resume_repo.create(
        db,
        user_id=user_id,
        filename="sample_resume.txt",
        storage_uri=f"{user_id}/sample_resume.txt",
        mime_type="text/plain",
    )
    version = resume_repo.create_version(
        db,
        resume_id=resume.id,
        raw_text=SAMPLE_RESUME_TEXT,
        parsed_facts=SAMPLE_RESUME_FACTS,
    )
    db.flush()
    return version.id


def _ensure_match_decision(
    db,
    *,
    user_id: str,
    job_id: str,
    resume_version_id: str | None,
    preset: dict,
) -> None:
    """Create a match decision artifact + job analysis (idempotent)."""
    artifact_type = "boss_match_decision"
    existing = (
        db.query(GeneratedArtifact)
        .filter(
            GeneratedArtifact.user_id == user_id,
            GeneratedArtifact.job_id == job_id,
            GeneratedArtifact.artifact_type == artifact_type,
        )
        .first()
    )
    if existing is not None:
        return

    run = agent_run_repo.create_run(
        db,
        user_id=user_id,
        workflow_type="boss_match_decision",
        status="succeeded",
        job_id=job_id,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
        finished_at=datetime.now(UTC),
    )
    db.flush()

    content = json.dumps(
        {
            "decision": preset["match_decision"],
            "score": preset["match_score"],
            "reasons": preset["match_reasons"],
            "risks": [],
            "missing_requirements": [],
            "opening_message": None,
        },
        ensure_ascii=False,
    )
    generated_artifact_repo.create(
        db,
        artifact_type=artifact_type,
        content=content,
        user_id=user_id,
        job_id=job_id,
        resume_version_id=resume_version_id,
        agent_run_id=run.id,
    )

    job_analysis_repo.create(
        db,
        job_id,
        agent_run_id=run.id,
        match_score=preset["match_score"],
        summary=preset["match_reasons"],
    )
    db.flush()


def _ensure_applications(
    db,
    *,
    user_id: str,
    jobs: list[tuple[str, dict]],
    resume_version_id: str | None,
) -> None:
    """Create application records + actions + outcomes for matched jobs."""
    matched = [
        (jid, p)
        for jid, p in jobs
        if p["match_decision"] == MatchDecision.communicate.value
    ]

    now = datetime.now(UTC)
    for idx, (job_id, preset) in enumerate(matched):
        existing = (
            db.query(ApplicationRecord)
            .filter(
                ApplicationRecord.user_id == user_id,
                ApplicationRecord.job_id == job_id,
            )
            .first()
        )
        if existing is not None:
            continue

        # First matched job: fully progressed to submitted + replied.
        # Second: in approval_required state (mid-funnel).
        if idx == 0:
            status = ApplicationStatus.submitted.value
            timeline = [
                application_repo.build_event(
                    type="status_change",
                    from_status="planned",
                    to_status=status,
                    summary="投递已提交（fake adapter）",
                ),
            ]
            record = application_repo.create_for_user(
                db,
                user_id=user_id,
                job_id=job_id,
                resume_version_id=resume_version_id,
                status=status,
                timeline=timeline,
            )
            db.flush()

            application_action_repo.create(
                db,
                application_id=record.id,
                user_id=user_id,
                action_type="platform_submit",
                status="approved",
                payload_preview={"company": preset["company"]},
                payload_hash="seed-hash-0001",
                source_snapshot={},
                approval=application_action_repo.build_approval_record(
                    approved_by="demo",
                    approved_at=now,
                    approved_payload_hash="seed-hash-0001",
                ),
                external_idempotency_key="seed-iek-0001",
            )
            db.flush()

            application_outcome_repo.create(
                db,
                application_id=record.id,
                user_id=user_id,
                outcome_type="replied",
                source="manual",
                occurred_at=now - timedelta(days=1),
                evidence="HR 已读消息并回复",
            )
        else:
            status = ApplicationStatus.approval_required.value
            timeline = [
                application_repo.build_event(
                    type="status_change",
                    from_status="planned",
                    to_status="preparing",
                    summary="正在准备投递材料",
                ),
                application_repo.build_event(
                    type="status_change",
                    from_status="preparing",
                    to_status=status,
                    summary="等待人工审批",
                ),
            ]
            record = application_repo.create_for_user(
                db,
                user_id=user_id,
                job_id=job_id,
                resume_version_id=resume_version_id,
                status=status,
                timeline=timeline,
            )
            db.flush()

            application_action_repo.create(
                db,
                application_id=record.id,
                user_id=user_id,
                action_type="platform_submit",
                status="approval_required",
                payload_preview={"company": preset["company"]},
                payload_hash="seed-hash-0002",
                source_snapshot={},
                external_idempotency_key="seed-iek-0002",
            )
        db.flush()


def seed() -> str:
    """Run the full seed. Returns the demo user id."""
    settings = get_settings()
    _guard(settings.app_env, force=False)

    with SessionLocal() as db:
        user_id = _ensure_demo_user(db)
        jobs = _ensure_jobs(db, user_id)
        resume_version_id = _ensure_resume(db, user_id)

        for job_id, preset in jobs:
            _ensure_match_decision(
                db,
                user_id=user_id,
                job_id=job_id,
                resume_version_id=resume_version_id,
                preset=preset,
            )

        _ensure_applications(
            db,
            user_id=user_id,
            jobs=jobs,
            resume_version_id=resume_version_id,
        )

        db.commit()

    return user_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data (idempotent).")
    parser.add_argument(
        "--i-know-this-is-demo",
        action="store_true",
        help="Required to run when APP_ENV=prod.",
    )
    args = parser.parse_args()

    settings = get_settings()
    _guard(settings.app_env, force=args.i_know_this_is_demo)

    user_id = seed()
    print(f"✅ Demo data seeded. Demo user id: {user_id}")
    print(f"   Username: {DEMO_USERNAME}  Password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
