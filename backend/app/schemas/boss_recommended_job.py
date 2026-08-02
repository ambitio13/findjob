"""Schemas for the BOSS recommended-job inspect flow.

These models define the wire contract for the ``POST /boss/recommended-jobs/
current/inspect`` endpoint, which reads the JD from the user's active BOSS
browser tab and upserts it into durable product data (JobPosting +
ApplicationRecord) with provenance.

Safety notes:

- No raw URLs, cookies, tokens, or page HTML are ever carried in these models.
  The JD dict returned by the userscript is already sanitized by
  :func:`sanitize_jd_result` at the bridge endpoint before it reaches the
  service layer.
- ``inspect_status`` communicates whether the JD was read successfully, too
  sparse to act on, or failed to read. The frontend uses this to decide what
  to show the user.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.schemas.api import ApplicationOut, JobOut
from app.schemas.common import BaseSchema


class InspectStatus(StrEnum):
    """Outcome of a BOSS recommended-job inspect call.

    - ``ok`` — JD was read and upserted into a Job (+Application) successfully.
    - ``jd_too_sparse`` — JD was read but missing required fields (title or
      description). No Job/Application was created.
    - ``read_failed`` — The userscript could not read the JD (disconnected,
      timeout, or extraction error). No Job/Application was created.
    """

    ok = "ok"
    jd_too_sparse = "jd_too_sparse"
    read_failed = "read_failed"


class InspectCurrentJobRequest(BaseModel):
    """Request payload for ``POST /boss/recommended-jobs/current/inspect``.

    ``resume_version_id`` is optional: when supplied, an ApplicationRecord is
    created (or reused) linking the upserted job to this resume version. When
    omitted, only the Job is upserted — the user can create an application
    later.
    """

    resume_version_id: str | None = None


class InspectJobOut(BaseSchema):
    """Response payload for the inspect endpoint.

    ``job`` and ``application`` are ``None`` when the inspect failed
    (``read_failed`` or ``jd_too_sparse``). ``is_new_job`` / ``is_new_application``
    let the frontend decide whether to show a "created" or "updated" badge.
    """

    job: JobOut | None = None
    application: ApplicationOut | None = None
    is_new_job: bool = False
    is_new_application: bool = False
    inspect_status: InspectStatus
    message: str | None = None
    agent_run_id: str | None = None


__all__ = [
    "InspectCurrentJobRequest",
    "InspectJobOut",
    "InspectStatus",
]
