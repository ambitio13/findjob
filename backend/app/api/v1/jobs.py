"""Jobs router with manual JD creation, list, and detail.

All endpoints are scoped to the current user: created jobs bind to
``current_user.id``, and list/detail only return records owned by the current
user. Cross-user access returns 404 (not 403) to avoid revealing resource
existence.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session
from app.db.models.models import JobPosting, UserProfile
from app.schemas.api import JobCreate, JobListOut, JobOut, PaginatedMeta

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListOut)
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobListOut:
    offset = (page - 1) * page_size
    base_filter = JobPosting.user_id == current_user.id
    rows = (
        db.execute(
            select(JobPosting)
            .where(base_filter)
            .order_by(JobPosting.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    total = db.execute(select(func.count()).select_from(JobPosting).where(base_filter)).scalar_one()
    return JobListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total),
        items=[JobOut.model_validate(r) for r in rows],
    )


@router.post("", response_model=JobOut, status_code=201)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobOut:
    job = JobPosting(
        user_id=current_user.id,
        platform=payload.platform,
        company=payload.company,
        title=payload.title,
        location=payload.location,
        salary_range=payload.salary_range,
        direction=payload.direction,
        jd_raw=payload.jd_raw,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> JobOut:
    job = db.get(JobPosting, job_id)
    if job is None or job.user_id != current_user.id:
        # 404 (not 403) to avoid revealing that the resource exists for
        # another user.
        raise HTTPException(status_code=404, detail="job not found")
    return JobOut.model_validate(job)
