"""BOSS recommended-job inspect router.

This router exposes the ``POST /boss/recommended-jobs/current/inspect``
endpoint, which reads the JD from the user's active BOSS browser tab (via the
userscript bridge) and upserts it into durable product data (JobPosting +
ApplicationRecord) with provenance.

Flow:

1. Create an ``AgentRun`` for provenance (``workflow_type=
   boss_recommended_job_inspect``, status=``running``).
2. Verify the userscript bridge is connected; if not, return ``read_failed``.
3. Read the JD from the browser via ``UserscriptBossPage.read_current_jd()``.
4. If the JD is ``None`` → ``read_failed``.
5. If the JD is too sparse (missing title or description) → ``jd_too_sparse``.
6. Upsert the job + application via ``inspect_current_job()``.
7. Update the AgentRun to ``succeeded`` with sanitized result metadata.
8. Return the job + application + inspect status.

Safety invariants:

- The endpoint is **authenticated** (``X-User-Id``) — unlike the userscript
  bridge endpoints, which are unauthenticated. The bridge endpoints only
  carry sanitized instruction/result data; this endpoint creates durable DB
  rows so it must be authenticated.
- No raw URL, cookie, or token is persisted. The JD dict is already sanitized
  by ``sanitize_jd_result`` at the bridge ``POST /result`` endpoint before it
  reaches the channel. ``page_url_hash`` (a sha256 digest) is used for dedup,
  never the raw URL.
- Cross-user access returns 404 (not 403).
- One ``application_id`` per inspect call — no batch paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.db.repositories import agent_run_repo
from app.platforms.boss.userscript_adapter import UserscriptBossPage
from app.platforms.boss.userscript_channel import get_channel
from app.schemas.api import ApplicationOut, JobOut
from app.schemas.boss_recommended_job import (
    InspectCurrentJobRequest,
    InspectJobOut,
    InspectStatus,
)
from app.services.boss_recommended_job_service import (
    WORKFLOW_TYPE,
    inspect_current_job,
)

_log = get_logger("app.api.v1.boss_recommended_jobs")

router = APIRouter(prefix="/boss/recommended-jobs", tags=["boss-recommended-jobs"])


@router.post("/current/inspect", response_model=InspectJobOut)
async def inspect_current_job_endpoint(
    payload: InspectCurrentJobRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> InspectJobOut:
    """Read the JD from the active BOSS tab and upsert it into product data.

    Creates an AgentRun for provenance, reads the JD via the userscript bridge,
    upserts the JobPosting (deduped by ``page_url_hash``), and optionally
    creates an ApplicationRecord when ``resume_version_id`` is supplied.

    Returns the job, the application (if any), and the inspect status.
    """
    # 1. Create the AgentRun for provenance.
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=datetime.now(UTC),
        result={
            "user_id": current_user.id,
            "resume_version_id": payload.resume_version_id,
        },
    )
    db.commit()
    db.refresh(run)

    # 2. Verify the userscript bridge is connected.
    channel = get_channel()
    if not channel.is_connected():
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error="bridge_not_connected",
            result={
                **(run.result or {}),
                "inspect_status": InspectStatus.read_failed.value,
            },
        )
        db.commit()
        return InspectJobOut(
            inspect_status=InspectStatus.read_failed,
            message="油猴脚本未连接，请确保已在 BOSS 页面安装并运行脚本。",
            agent_run_id=run.id,
        )

    # 3. Read the JD from the browser.
    page = UserscriptBossPage(channel)
    try:
        jd_dict = await page.read_current_jd()
    except Exception as exc:
        _log.warning(
            "boss_recommended_job.read_jd_error",
            user_id=current_user.id,
            error=str(exc),
        )
        jd_dict = None
    finally:
        channel.clear()

    # 4-6. Inspect + upsert.
    result = inspect_current_job(
        db,
        current_user,
        jd_dict=jd_dict,
        agent_run_id=run.id,
        resume_version_id=payload.resume_version_id,
    )

    # 7. Update the AgentRun.
    run_result = {
        **(run.result or {}),
        "inspect_status": result.status.value,
        "is_new_job": result.is_new_job,
        "is_new_application": result.is_new_application,
    }
    if result.job is not None:
        run_result["job_id"] = result.job.id
    if result.application is not None:
        run_result["application_id"] = result.application.id

    if result.status == InspectStatus.ok:
        agent_run_repo.update_status(
            db,
            run,
            status="succeeded",
            finished_at=datetime.now(UTC),
            result=run_result,
        )
    else:
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error=result.status.value,
            result=run_result,
        )
    db.commit()

    # 8. Build response.
    return InspectJobOut(
        job=JobOut.model_validate(result.job) if result.job else None,
        application=ApplicationOut.model_validate(result.application)
        if result.application
        else None,
        is_new_job=result.is_new_job,
        is_new_application=result.is_new_application,
        inspect_status=result.status,
        message=result.message,
        agent_run_id=run.id,
    )
