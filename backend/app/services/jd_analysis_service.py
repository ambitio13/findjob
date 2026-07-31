"""JD analysis service — context loader (Phase 1).

This module currently owns only the *context loading* portion of the
resume-aware JD analysis workflow: read and verify ownership of the current
user's profile, the target job, and the selected resume version, then build a
``JdAnalysisContext`` ready for the prompt builder and executor.

The full workflow orchestration (model call, persistence, ``AgentRun`` /
``AgentStep`` lifecycle) belongs to Phase 4 and is intentionally absent here.

Ownership rules (design.md §5.1 error table):

- job missing or not owned by the current user → ``404 job not found``;
- resume version missing, or its parent resume not owned by the current user
  → ``404 resume version not found``;
- resume version ``raw_text`` empty/blank → ``422 resume version has no
  parsed text``.

Cross-user access returns 404 (not 403) to avoid revealing resource existence,
matching the convention used in ``app/api/v1/jobs.py`` and ``resumes.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.prompts.jd_analysis import JdAnalysisContext
from app.core.logging import get_logger
from app.db.models.models import JobPosting, Resume, ResumeVersion, UserProfile
from app.db.repositories import resume_repo

_log = get_logger("app.services.jd_analysis_service")


def _job_to_dict(job: JobPosting) -> dict[str, Any]:
    """Project a ``JobPosting`` into the compact job dict used by the prompt."""
    return {
        "id": job.id,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "salary_range": job.salary_range,
        "direction": job.direction,
        "jd_raw": job.jd_raw,
    }


def _profile_to_dict(profile: UserProfile) -> dict[str, Any]:
    """Project a ``UserProfile`` into the compact profile dict for the prompt."""
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "email": profile.email,
        "career_direction": profile.career_direction,
        "base_location": profile.base_location,
        "preferred_locations": profile.preferred_locations,
        "salary_min": profile.salary_min,
        "salary_max": profile.salary_max,
        "strengths": profile.strengths,
        "constraints": profile.constraints,
    }


def _resume_to_dict(resume: Resume, version: ResumeVersion) -> dict[str, Any]:
    """Project a resume + version into the resume dict for the prompt."""
    facts = version.parsed_facts or {}
    return {
        "resume_id": resume.id,
        "resume_version_id": version.id,
        "filename": resume.filename,
        "parser_status": facts.get("_parser_status"),
        "parser_name": facts.get("_parser"),
        "raw_text": version.raw_text or "",
        "parsed_facts": facts,
    }


def load_jd_analysis_context(
    db: Session,
    current_user: UserProfile,
    job_id: str,
    resume_version_id: str,
) -> JdAnalysisContext:
    """Load and verify all sources, returning a ready ``JdAnalysisContext``.

    Raises ``HTTPException`` (404/422) on ownership or data-quality failures.
    Does not call the model and does not persist anything.
    """
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        _log.info("jd_analysis.job_not_found", user_id=current_user.id, job_id=job_id)
        raise HTTPException(status_code=404, detail="job not found")

    version = db.get(ResumeVersion, resume_version_id)
    if version is None:
        _log.info(
            "jd_analysis.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    resume = resume_repo.get(db, version.resume_id)
    if resume is None or resume.user_id != current_user.id:
        # The version row existed but its parent resume is not owned by the
        # current user. Treat as not-found to avoid revealing existence.
        _log.info(
            "jd_analysis.resume_version_not_found",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
            resume_id=version.resume_id,
        )
        raise HTTPException(status_code=404, detail="resume version not found")

    raw_text = (version.raw_text or "").strip()
    if not raw_text:
        _log.info(
            "jd_analysis.resume_version_no_text",
            user_id=current_user.id,
            resume_version_id=resume_version_id,
        )
        raise HTTPException(status_code=422, detail="resume version has no parsed text")

    return JdAnalysisContext(
        user_id=current_user.id,
        profile=_profile_to_dict(current_user),
        job=_job_to_dict(job),
        resume=_resume_to_dict(resume, version),
    )
