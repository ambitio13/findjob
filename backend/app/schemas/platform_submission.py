"""Pydantic schemas for the platform guided-submit workflow.

Two groups of schemas live here:

1. API request/response schemas used by the
   ``POST /applications/{id}/platform-submissions/prepare`` endpoint (and the
   later submit/abort endpoints).
2. Outbound view of the persisted ``ApplicationAction`` so the frontend can
   render the filled preview and approval state from a single read.

No cookies, tokens, credentials, raw JD, raw resume, or page HTML may appear in
any of these schemas. The ``FilledSubmissionSnapshot`` is sanitized at the
adapter boundary; the action preview carries only what the user is approving.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.application_action import (
    ApplicationActionOut,
    ApplicationActionPreview,
)
from app.schemas.readiness import RunReadinessRunSummary


class PlatformSubmissionPrepareRequest(BaseModel):
    """Request payload for the prepare endpoint.

    ``target_resource`` is the platform resource the form lives on (job posting
    URL or HR conversation id). ``selected_artifact_ids`` and ``outgoing_text``
    are the readiness artifact IDs and outgoing HR-message text the prepare flow
    will fill into the platform form in dry-run mode.

    No raw credentials, cookies, or session data are accepted here.
    """

    target_resource: str = Field(
        min_length=1,
        description="Platform resource the form lives on (job posting URL or HR conversation id).",
    )
    selected_artifact_ids: list[str] = Field(default_factory=list)
    outgoing_text: str | None = None
    resume_file_reference: str | None = None


class PlatformSubmissionPrepareResponse(BaseModel):
    """Immediate response for the prepare endpoint (enqueue-and-poll).

    The endpoint creates a ``queued`` ``AgentRun`` and enqueues the platform
    guided-submit prepare job to the worker queue, then returns immediately with
    this response. The frontend polls ``GET /agent-runs/{run_id}/detail`` until
    the run reaches a terminal status (``succeeded`` or ``failed``), then
    hydrates the prepared action from the persisted ``ApplicationAction`` row.
    """

    run: RunReadinessRunSummary
    application_id: str
    target_platform: str = "boss"
    mode: str = "dry_run"


class PlatformSubmissionDetailResponse(BaseModel):
    """Detail response for a prepared platform submission.

    Surfaces the prepared action (with its filled preview + approval state) and
    the run that produced it, so the frontend can render the guided-submit panel
    from a single read.
    """

    run: RunReadinessRunSummary
    application_id: str
    action: ApplicationActionOut | None = None
    preview: ApplicationActionPreview | None = None


class PlatformSubmissionSubmitResponse(BaseModel):
    """Response for the final-submit endpoint.

    Carries the run reference (always the prepare run) and the action after the
    adapter final submit / failure was persisted. The action's
    ``external_result`` holds the sanitized terminal result. No cookies, tokens,
    credentials, or page HTML are ever included.
    """

    run: RunReadinessRunSummary
    application_id: str
    action: ApplicationActionOut


class PlatformSubmissionAbortResponse(BaseModel):
    """Response for the abort endpoint.

    Aborting cancels the in-flight submit and returns the action in its
    post-abort state (``revoked`` so it can no longer authorize execution). No
    platform side effect is performed.
    """

    run: RunReadinessRunSummary
    application_id: str
    action: ApplicationActionOut


__all__ = [
    "PlatformSubmissionAbortResponse",
    "PlatformSubmissionDetailResponse",
    "PlatformSubmissionPrepareRequest",
    "PlatformSubmissionPrepareResponse",
    "PlatformSubmissionSubmitResponse",
]
