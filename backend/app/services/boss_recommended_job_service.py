"""BOSS recommended-job inspect service.

This service turns the sanitized JD dict (read from the browser via the
userscript bridge) into durable product data:

1. **Dedup** by ``platform="boss"`` + ``external_id = page_url_hash`` —
   re-reading the same BOSS page returns the existing job instead of creating a
   duplicate. No migration is needed because ``JobPosting.external_id`` already
   exists.
2. **Upsert** the ``JobPosting`` row — update JD fields in place if the job
   already exists, or create a new one.
3. **Provenance** is stored in ``jd_normalized._source`` (not new DB columns),
   consistent with the existing ``_extraction`` provenance pattern in
   :mod:`app.services.jd_parse_service`.
4. **Application record** — when ``resume_version_id`` is supplied, create (or
   reuse) an ``ApplicationRecord`` linking the job to that resume version.

Safety invariants:

- Cross-user access returns 404 (not 403) — the dedup query scopes to
  ``user_id``.
- No raw URL is persisted — ``external_id`` stores the ``page_url_hash`` (a
  sha256 digest), never the raw BOSS URL.
- No raw HTML is persisted — the JD dict is already sanitized by
  :func:`sanitize_jd_result` at the bridge endpoint before it reaches this
  service.
- One ``application_id`` per inspect call — no batch paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.models import ApplicationRecord, JobPosting, UserProfile
from app.db.repositories import application_repo, job_repo, resume_repo
from app.schemas.application import ApplicationStatus
from app.schemas.boss_recommended_job import InspectStatus

_log = get_logger("app.services.boss_recommended_job_service")

#: Workflow type for AgentRun records created by this service.
WORKFLOW_TYPE = "boss_recommended_job_inspect"

#: The platform value for jobs created from the BOSS recommended-job flow.
_BOSS_PLATFORM = "boss"

#: The source kind stored in ``jd_normalized._source``.
_SOURCE_KIND = "boss_userscript_read_jd"

#: Minimum JD fields required to create a job. A JD missing ``title`` or
#: ``description`` is flagged ``jd_too_sparse`` and no job/application is
#: created — the user can retry when the page has fully loaded.
_REQUIRED_FIELDS = ("title", "description")


def is_jd_too_sparse(jd_dict: dict[str, Any] | None) -> bool:
    """Return ``True`` if the JD dict is missing required minimum fields.

    A BOSS recommended-job JD must have at least a ``title`` and a
    ``description``. Missing either means the page did not fully load, the
    userscript extracted from the wrong page, or the BOSS layout changed — in
    all cases the inspect returns ``jd_too_sparse`` and no job/application is
    created.
    """
    if not isinstance(jd_dict, dict):
        return True
    for field in _REQUIRED_FIELDS:
        value = jd_dict.get(field)
        if not value or not str(value).strip():
            return True
    return False


def _build_jd_raw(jd_dict: dict[str, Any]) -> str:
    """Build a plain-text ``jd_raw`` string from the structured JD dict.

    ``JobPosting.jd_raw`` is a NOT NULL text column. For BOSS-read jobs, the
    authoritative structured data lives in ``jd_normalized``; ``jd_raw`` is a
    readable concatenation of the sanitized fields so the user can see what was
    read without the model needing to re-parse.
    """
    parts: list[str] = []
    title = jd_dict.get("title")
    company = jd_dict.get("company")
    if title:
        parts.append(f"职位：{title}")
    if company:
        parts.append(f"公司：{company}")
    location = jd_dict.get("location")
    salary = jd_dict.get("salary")
    if location:
        parts.append(f"地点：{location}")
    if salary:
        parts.append(f"薪资：{salary}")
    experience = jd_dict.get("experience")
    education = jd_dict.get("education")
    if experience:
        parts.append(f"经验：{experience}")
    if education:
        parts.append(f"学历：{education}")
    skills = jd_dict.get("skills")
    if isinstance(skills, list) and skills:
        parts.append(f"技能：{', '.join(skills)}")
    description = jd_dict.get("description")
    if description:
        parts.append(f"描述：{description}")
    return "\n".join(parts) if parts else "(空)"


def _build_jd_normalized(
    jd_dict: dict[str, Any],
    *,
    page_url_hash: str | None,
    agent_run_id: str | None,
) -> dict[str, Any]:
    """Build the ``jd_normalized`` JSON with provenance under ``_source``.

    Structure::

        {
            "_source": {
                "source_kind": "boss_userscript_read_jd",
                "page_url_hash": "sha256:abcdef12",
                "read_at": "2026-08-03T...",
                "agent_run_id": "run_...",
            },
            "fields": {
                "title": "...", "company": "...", "location": "...",
                "salary": "...", "experience": "...", "education": "...",
                "skills": [...], "description": "...",
            },
        }

    ``page_url_hash`` is already a sha256 digest from the userscript — never
    the raw URL. ``agent_run_id`` links to the AgentRun created for this
    inspect call.
    """
    return {
        "_source": {
            "source_kind": _SOURCE_KIND,
            "page_url_hash": page_url_hash,
            "read_at": datetime.now(UTC).isoformat(),
            "agent_run_id": agent_run_id,
        },
        "fields": {
            "title": jd_dict.get("title"),
            "company": jd_dict.get("company"),
            "location": jd_dict.get("location"),
            "salary": jd_dict.get("salary"),
            "experience": jd_dict.get("experience"),
            "education": jd_dict.get("education"),
            "skills": jd_dict.get("skills") or [],
            "description": jd_dict.get("description"),
        },
    }


def upsert_job_from_browser_jd(
    db: Session,
    *,
    user_id: str,
    jd_dict: dict[str, Any],
    agent_run_id: str | None,
) -> tuple[JobPosting, bool]:
    """Upsert a ``JobPosting`` from a sanitized browser JD dict.

    Dedup key: ``platform="boss"`` + ``external_id = page_url_hash``. If an
    existing job is found, its JD fields are updated in place. Otherwise a new
    job is created.

    Returns ``(job, is_new)``.

    Raises ``ValueError`` if the JD dict has no ``page_url_hash`` — every
    BOSS-read JD must carry one for dedup. The caller should check
    :func:`is_jd_too_sparse` before calling this function.
    """
    page_url_hash = jd_dict.get("page_url_hash")
    if not isinstance(page_url_hash, str) or not page_url_hash.strip():
        raise ValueError("JD dict missing page_url_hash for dedup")

    title = str(jd_dict.get("title") or "").strip()
    company = str(jd_dict.get("company") or "").strip() or "(未知公司)"
    location = jd_dict.get("location")
    salary = jd_dict.get("salary")

    jd_raw = _build_jd_raw(jd_dict)
    jd_normalized = _build_jd_normalized(
        jd_dict,
        page_url_hash=page_url_hash,
        agent_run_id=agent_run_id,
    )

    existing = job_repo.find_by_platform_and_external_id(
        db,
        user_id=user_id,
        platform=_BOSS_PLATFORM,
        external_id=page_url_hash,
    )
    if existing is not None:
        job_repo.update(
            db,
            existing,
            company=company,
            title=title,
            location=location,
            salary_range=salary,
            platform=_BOSS_PLATFORM,
            external_id=page_url_hash,
            jd_raw=jd_raw,
            jd_normalized=jd_normalized,
        )
        _log.info(
            "boss_recommended_job.job_updated",
            user_id=user_id,
            job_id=existing.id,
            page_url_hash=page_url_hash,
        )
        return existing, False

    job = job_repo.create(
        db,
        user_id=user_id,
        company=company,
        title=title,
        jd_raw=jd_raw,
        platform=_BOSS_PLATFORM,
        location=location,
        salary_range=salary,
        jd_normalized=jd_normalized,
    )
    # Set external_id for future dedup — job_repo.create does not accept it.
    job.external_id = page_url_hash
    db.flush()
    _log.info(
        "boss_recommended_job.job_created",
        user_id=user_id,
        job_id=job.id,
        page_url_hash=page_url_hash,
    )
    return job, True


def _verify_resume_version_ownership(
    db: Session, current_user: UserProfile, resume_version_id: str
) -> None:
    """Verify the resume version is owned by the current user (404 on failure).

    Mirrors :func:`app.services.application_service._verify_resume_version_ownership`.
    """
    from app.db.models.models import ResumeVersion

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        raise _not_found("resume version not found")
    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        raise _not_found("resume version not found")


def _not_found(detail: str) -> Exception:
    """Return an HTTPException for 404 responses (not 403)."""
    from fastapi import HTTPException

    return HTTPException(status_code=404, detail=detail)


class InspectResult:
    """Result of :func:`inspect_current_job`.

    Carries the upserted job, the application record (if any), and flags so
    the API layer can build the :class:`InspectJobOut` response.
    """

    __slots__ = (
        "job",
        "application",
        "is_new_job",
        "is_new_application",
        "status",
        "message",
    )

    def __init__(
        self,
        *,
        job: JobPosting | None = None,
        application: ApplicationRecord | None = None,
        is_new_job: bool = False,
        is_new_application: bool = False,
        status: InspectStatus = InspectStatus.ok,
        message: str | None = None,
    ) -> None:
        self.job = job
        self.application = application
        self.is_new_job = is_new_job
        self.is_new_application = is_new_application
        self.status = status
        self.message = message


def inspect_current_job(
    db: Session,
    current_user: UserProfile,
    *,
    jd_dict: dict[str, Any] | None,
    agent_run_id: str | None,
    resume_version_id: str | None = None,
) -> InspectResult:
    """Inspect the JD currently shown in the user's BOSS tab.

    This is the core orchestration method for the BOSS recommended-job flow:

    1. If ``jd_dict`` is ``None`` → ``read_failed`` (the userscript could not
       read the JD).
    2. If the JD is too sparse (missing title or description) →
       ``jd_too_sparse``.
    3. Upsert the ``JobPosting`` (dedup by ``page_url_hash``).
    4. If ``resume_version_id`` is supplied, verify ownership and create (or
       reuse) an ``ApplicationRecord`` linking the job to that resume version.

    All DB changes are committed within this call. The caller (API endpoint)
    creates the ``AgentRun`` for provenance and passes its id.

    Returns an :class:`InspectResult` with the upserted job, the application
    record (if any), and the inspect status.
    """
    # 1. Read failure.
    if jd_dict is None:
        return InspectResult(
            status=InspectStatus.read_failed,
            message="无法读取职位详情，请确认油猴脚本已连接且页面已加载完成。",
        )

    # 2. Sparse JD.
    if is_jd_too_sparse(jd_dict):
        return InspectResult(
            status=InspectStatus.jd_too_sparse,
            message="职位详情缺少标题或描述，无法创建投递记录。请等待页面完全加载后重试。",
        )

    # 3. Verify resume version ownership (when supplied) BEFORE upserting the
    #    job so a bad resume_version_id does not leave a half-created job.
    if resume_version_id is not None:
        _verify_resume_version_ownership(db, current_user, resume_version_id)

    # 4. Upsert the job.
    try:
        job, is_new_job = upsert_job_from_browser_jd(
            db,
            user_id=current_user.id,
            jd_dict=jd_dict,
            agent_run_id=agent_run_id,
        )
    except ValueError as exc:
        _log.warning(
            "boss_recommended_job.upsert_failed",
            user_id=current_user.id,
            error=str(exc),
        )
        return InspectResult(
            status=InspectStatus.read_failed,
            message="职位信息缺少页面标识，无法创建投递记录。",
        )

    # 5. Create or reuse an application record (when resume_version_id supplied).
    application: ApplicationRecord | None = None
    is_new_application = False
    if resume_version_id is not None:
        existing_app = application_repo.find_duplicate(
            db,
            user_id=current_user.id,
            job_id=job.id,
            resume_version_id=resume_version_id,
        )
        if existing_app is not None:
            application = existing_app
        else:
            event = application_repo.build_event(
                type="created",
                actor="agent",
                to_status=ApplicationStatus.planned.value,
                summary="BOSS 推荐职位投递记录已创建",
                metadata={
                    "job_id": job.id,
                    "resume_version_id": resume_version_id,
                    "agent_run_id": agent_run_id,
                    "source": "boss_recommended_job_inspect",
                },
            )
            application = application_repo.create_for_user(
                db,
                user_id=current_user.id,
                job_id=job.id,
                resume_version_id=resume_version_id,
                status=ApplicationStatus.planned.value,
                timeline=[event],
            )
            is_new_application = True

    db.commit()
    db.refresh(job)
    if application is not None:
        db.refresh(application)

    _log.info(
        "boss_recommended_job.inspect_ok",
        user_id=current_user.id,
        job_id=job.id,
        is_new_job=is_new_job,
        application_id=application.id if application else None,
        is_new_application=is_new_application,
    )

    return InspectResult(
        job=job,
        application=application,
        is_new_job=is_new_job,
        is_new_application=is_new_application,
        status=InspectStatus.ok,
    )


__all__ = [
    "InspectResult",
    "WORKFLOW_TYPE",
    "inspect_current_job",
    "is_jd_too_sparse",
    "upsert_job_from_browser_jd",
]
