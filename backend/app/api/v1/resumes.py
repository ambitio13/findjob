"""Resumes router: upload, list, detail, version listing, and re-extract.

All endpoints are scoped to the current user. Uploaded files are persisted to
local disk and a ``Resume`` + ``ResumeVersion`` row pair is created per upload.
Parser output is honest: ``raw_text`` is real extracted text;
``parsed_facts`` stores parser telemetry only (no invented facts). After a
successful text extraction, an auditable model-backed resume fact extraction
runs as an automatic **background** workflow (see ``resume_fact_service``):
the upload endpoint writes ``_extraction.status="pending"``, schedules the
runner, and returns immediately so the request never blocks on the model call.
The explicit re-extract endpoint stays synchronous. Resume content is never
logged; only IDs and lengths are logged.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db_session, get_model_gateway_dep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.db.repositories import resume_repo
from app.models_gateway.base import ModelGateway
from app.schemas.api import PaginatedMeta
from app.schemas.profile_draft import ApplyProfileDraftRequest, ApplyProfileDraftResponse
from app.schemas.resume import (
    ResumeDetailOut,
    ResumeListOut,
    ResumeOut,
    ResumeVersionListItem,
    ResumeVersionOut,
)
from app.services import profile_draft_service, resume_fact_service, resume_parser, resume_storage

router = APIRouter(prefix="/resumes", tags=["resumes"])
_log = get_logger("app.api.v1.resumes")


@router.post("", response_model=ResumeDetailOut, status_code=201)
async def upload_resume(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> ResumeDetailOut:
    """Upload a resume file, parse it, and schedule structured fact extraction.

    Validates extension and size before reading the body. The file is persisted
    to local disk; a ``Resume`` and its first ``ResumeVersion`` are created.
    When text extraction succeeds the version is marked
    ``_extraction.status="pending"`` and an auditable model-backed resume fact
    extraction is scheduled as a post-response background task — the upload
    response returns immediately and never blocks on the model call. The
    frontend polls ``GET /resumes/{id}`` to observe
    ``pending`` → ``running`` → ``succeeded``/``failed``. Unsupported/empty
    raw text is marked ``not_run`` and no extraction is scheduled. Extraction
    failure (in the background) persists a failed ``AgentRun`` but leaves the
    uploaded resume visible and usable.
    """
    settings = get_settings()
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=422, detail="filename is required")
    display_filename = resume_storage.display_filename(filename)

    ext = _extension(display_filename)
    if ext not in resume_parser.ACCEPTED_EXTENSIONS:
        # Non-resume extensions (e.g. .html) are rejected outright.
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type: .{ext}" if ext else "unsupported file type",
        )

    content = await _read_with_size_limit(file, settings.resume_max_size_mb)
    mime_type = file.content_type

    try:
        result = resume_parser.parse_resume(content, mime_type, filename)
    except Exception as exc:  # noqa: BLE001 — corrupt PDF/DOCX surfaces as 422
        raise HTTPException(
            status_code=422, detail=f"failed to parse file: {type(exc).__name__}"
        ) from exc

    resume = resume_repo.create(
        db,
        user_id=current_user.id,
        filename=display_filename,
        storage_uri=None,
        mime_type=mime_type,
    )
    # Persist the file after the Resume row exists so we have a stable id for
    # the on-disk path. If parsing was unsupported we still store the bytes so
    # the user can later re-download; raw_text stays empty.
    storage_uri = resume_storage.save_upload(
        upload_dir=settings.resume_upload_dir,
        user_id=current_user.id,
        resume_id=resume.id,
        filename=display_filename,
        content=content,
    )
    resume.storage_uri = storage_uri
    version = resume_repo.create_version(
        db,
        resume_id=resume.id,
        raw_text=result.raw_text,
        parsed_facts=result.to_facts(),
    )
    db.commit()
    db.refresh(resume)
    db.refresh(version)

    # Decouple model-backed extraction from the upload HTTP request. For
    # parsed raw text, mark pending + schedule the background runner so the
    # response returns well within the frontend timeout budget. Unsupported /
    # empty raw text is marked not_run and no extraction is scheduled.
    raw_text = (result.raw_text or "").strip()
    if raw_text:
        resume_fact_service.mark_extraction_pending(db, version)
        _schedule_extraction(
            background_tasks,
            resume_id=resume.id,
            version_id=version.id,
            user_id=current_user.id,
            gateway=gateway,
        )
    else:
        resume_fact_service.mark_extraction_not_run(db, version)
        db.refresh(version)

    _log.info(
        "resume.uploaded",
        user_id=current_user.id,
        resume_id=resume.id,
        filename=display_filename,
        mime_type=mime_type,
        size_bytes=len(content),
        parser_name=result.parser_name,
        raw_text_len=len(result.raw_text),
    )
    return ResumeDetailOut(
        id=resume.id,
        filename=resume.filename,
        mime_type=resume.mime_type,
        storage_uri=resume.storage_uri,
        created_at=resume.created_at,
        latest_version=_version_out(version),
    )


@router.get("", response_model=ResumeListOut)
def list_resumes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ResumeListOut:
    """List the current user's resumes (no ``raw_text``)."""
    rows, total = resume_repo.list_for_user(db, current_user.id, page, page_size)
    items = [_resume_out(db, r) for r in rows]
    return ResumeListOut(
        meta=PaginatedMeta(page=page, page_size=page_size, total=total),
        items=items,
    )


@router.get("/{resume_id}", response_model=ResumeDetailOut)
def get_resume(
    resume_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ResumeDetailOut:
    """Return resume detail, including the latest version's ``raw_text``."""
    resume = resume_repo.get(db, resume_id)
    if resume is None or resume.user_id != current_user.id:
        # 404 (not 403) to avoid revealing that the resource exists for
        # another user.
        raise HTTPException(status_code=404, detail="resume not found")
    latest = resume_repo.latest_version(db, resume.id)
    return ResumeDetailOut(
        id=resume.id,
        filename=resume.filename,
        mime_type=resume.mime_type,
        storage_uri=resume.storage_uri,
        created_at=resume.created_at,
        latest_version=_version_out(latest) if latest else None,
    )


@router.get("/{resume_id}/versions", response_model=list[ResumeVersionListItem])
def list_resume_versions(
    resume_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> list[ResumeVersionListItem]:
    """List versions for a resume without ``raw_text``."""
    resume = resume_repo.get(db, resume_id)
    if resume is None or resume.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="resume not found")
    versions = resume_repo.list_versions(db, resume.id)
    return [
        ResumeVersionListItem(
            id=v.id,
            version_no=v.version_no,
            created_at=v.created_at,
            parser_status=(v.parsed_facts or {}).get("_parser_status"),
            parser_name=(v.parsed_facts or {}).get("_parser"),
        )
        for v in versions
    ]


@router.post(
    "/{resume_id}/versions/{version_id}/extract",
    response_model=ResumeDetailOut,
)
async def reextract_resume_facts(
    resume_id: str,
    version_id: str,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway_dep),
) -> ResumeDetailOut:
    """Re-run structured fact extraction on an existing resume version.

    User-scoped (404 on cross-user). Creates a fresh ``AgentRun`` and refreshes
    ``parsed_facts.facts`` on the existing ``raw_text``. Unlike upload,
    extraction is the primary action: a model/provider/schema failure persists
    a failed ``AgentRun`` and then returns 502.
    """
    resume, version = resume_fact_service.load_resume_version_for_user(
        db, current_user, resume_id, version_id
    )
    await resume_fact_service.extract_resume_facts(
        db, resume, version, gateway, raise_on_failure=True
    )
    db.refresh(version)
    return ResumeDetailOut(
        id=resume.id,
        filename=resume.filename,
        mime_type=resume.mime_type,
        storage_uri=resume.storage_uri,
        created_at=resume.created_at,
        latest_version=_version_out(version),
    )


@router.post(
    "/{resume_id}/versions/{version_id}/apply-profile-draft",
    response_model=ApplyProfileDraftResponse,
)
def apply_profile_draft(
    resume_id: str,
    version_id: str,
    payload: ApplyProfileDraftRequest,
    db: Session = Depends(get_db_session),
    current_user: UserProfile = Depends(get_current_user),
) -> ApplyProfileDraftResponse:
    """Preview or apply resume facts to the current user's profile.

    User-scoped (404 on cross-user resume/version access). Default
    ``confirm=false`` returns a preview diff without writing. ``confirm=true``
    writes allowed changes through the same repository path used by
    ``PATCH /users/me``. Non-empty existing profile fields are not overwritten
    unless ``overwrite=true``.
    """
    resume, version = resume_fact_service.load_resume_version_for_user(
        db, current_user, resume_id, version_id
    )
    return profile_draft_service.apply_profile_draft(db, current_user, version, payload)


# --- helpers ---------------------------------------------------------------


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    if dot < 0:
        return ""
    return filename[dot + 1 :].lower()


def _schedule_extraction(
    background_tasks: BackgroundTasks,
    *,
    resume_id: str,
    version_id: str,
    user_id: str,
    gateway: ModelGateway,
) -> None:
    """Schedule the background resume fact extraction.

    Uses a FastAPI ``BackgroundTasks`` entry so the runner executes after the
    upload response is sent. A short-lived ``asyncio`` task wrapper gives the
    async runner its own event-loop context inside the background-task slot.
    The ``gateway`` resolved by the request's DI is passed in so tests can
    override it through ``dependency_overrides`` exactly like the sync path.
    """
    run_id = uuid.uuid4().hex
    _log.info(
        "resume_fact.scheduled",
        resume_id=resume_id,
        version_id=version_id,
        user_id=user_id,
        schedule_id=run_id,
    )
    background_tasks.add_task(
        _run_extraction_task,
        resume_id,
        version_id,
        user_id,
        gateway,
    )


def _run_extraction_task(
    resume_id: str,
    version_id: str,
    user_id: str,
    gateway: ModelGateway,
) -> None:
    """Sync adapter that drives the async background runner on a fresh loop.

    ``BackgroundTasks`` executes callables synchronously after the response;
    this wrapper creates a dedicated event loop for the async runner so it
    never touches the request's loop (already closed by then).
    """
    asyncio.run(
        resume_fact_service.run_resume_fact_extraction_background(
            resume_id=resume_id,
            version_id=version_id,
            user_id=user_id,
            gateway=gateway,
        )
    )


async def _read_with_size_limit(file: UploadFile, max_size_mb: int) -> bytes:
    """Read ``file`` fully, raising 413 if it exceeds ``max_size_mb``."""
    max_bytes = max_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"file exceeds {max_size_mb} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _version_out(version) -> ResumeVersionOut:  # type: ignore[no-untyped-def]
    return ResumeVersionOut(
        id=version.id,
        version_no=version.version_no,
        parsed_facts=version.parsed_facts,
        raw_text=version.raw_text,
        created_at=version.created_at,
    )


def _resume_out(db: Session, resume) -> ResumeOut:  # type: ignore[no-untyped-def]
    latest = resume_repo.latest_version(db, resume.id)
    return ResumeOut(
        id=resume.id,
        filename=resume.filename,
        mime_type=resume.mime_type,
        created_at=resume.created_at,
        latest_version_no=latest.version_no if latest else None,
    )
