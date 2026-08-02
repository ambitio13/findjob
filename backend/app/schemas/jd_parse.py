"""Pydantic request/response schemas for the JD paste parse endpoint.

The parse endpoint accepts raw JD text, delegates to
:mod:`app.services.jd_parse_service`, and returns the parsed draft fields plus
an ``AgentRun`` summary. Model/provider/schema failures are recoverable: the
endpoint returns HTTP 200 with a failed run and empty typed fields so the user
can still fall back to manual entry. Request validation (blank JD) returns 422
before the service is entered.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from app.schemas.api import JobOut
from app.schemas.jd_paste_facts import JdPasteFactsModelOutput


class JdParseRequest(BaseModel):
    """Inbound body for ``POST /api/v1/jobs/parse``.

    ``raw_jd`` must contain non-whitespace text; blank input is a request
    validation error (422) and creates no ``AgentRun``.
    """

    raw_jd: str
    platform: str | None = None

    @field_validator("raw_jd")
    @classmethod
    def _raw_jd_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("raw_jd must not be blank")
        return v


class JdParseRunSummary(BaseModel):
    """Lightweight run summary surfaced in the parse response.

    Carries just enough for the frontend to display the run status and link to
    the full run detail (``/agent-runs/{id}``) for step-level auditing.
    """

    id: str
    status: str
    error: str | None = None

    model_config = {"from_attributes": True}


class JdParseExtraction(BaseModel):
    """Parse provenance carried alongside the draft fields.

    The frontend persists this verbatim into ``jd_normalized._extraction`` so
    the saved job carries an auditable link back to the AgentRun and model that
    produced the draft (design.md §"jd_normalized shape").
    """

    status: Literal["succeeded", "failed"]
    run_id: str
    parsed_at: str
    prompt_version: str
    provider: str | None = None
    model: str | None = None


class JdParseResponse(BaseModel):
    """Response for ``POST /api/v1/jobs/parse``.

    On success: ``status="succeeded"``, ``fields`` populated, ``run.status``
    succeeded, ``extraction`` carries the provenance the frontend persists into
    ``jd_normalized._extraction``. On recoverable model/schema failure:
    ``status="failed"``, ``fields`` is the empty typed shape (so the frontend
    contract is stable), and the failed run is surfaced for inspection.
    ``raw_jd`` is echoed back so the form can re-submit it unchanged on save.
    """

    status: Literal["succeeded", "failed"]
    run: JdParseRunSummary
    fields: JdPasteFactsModelOutput
    extraction: JdParseExtraction
    raw_jd: str


class JdParseSubmitResponse(BaseModel):
    """Immediate response for ``POST /api/v1/jobs/parse`` (enqueue-and-poll).

    The endpoint creates a ``queued`` ``AgentRun`` and enqueues the parse job
    to the worker queue, then returns immediately with this response. The
    frontend polls ``GET /agent-runs/{run_id}/detail`` (or
    ``GET /agent-runs/{run_id}``) until the run reaches a terminal status
    (``succeeded`` or ``failed``), then hydrates the parsed fields from
    ``AgentRun.result.fields`` on success.

    ``raw_jd`` is echoed back so the form retains the user's input while
    polling. ``platform`` is echoed for symmetry.
    """

    run: JdParseRunSummary
    job: JobOut
    raw_jd: str
    platform: str | None = None
