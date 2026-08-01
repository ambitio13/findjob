"""Typed queue payload models.

Every job sent through the queue runtime carries one of these payloads. The
base model captures the cross-cutting fields every workflow needs; concrete
workflows add their own resource IDs. Payloads are deliberately small and
serializable — they reference durable DB rows by ID and never carry raw model
inputs (raw JD text, raw resume content) so a leaked queue entry cannot expose
user content.

Contract (design.md):

- API code creates the durable DB state (``AgentRun`` + owned rows) first,
  then enqueues a payload that references it by ID.
- Worker code re-loads owned resources by ID from a fresh session.
- ``idempotency_key`` lets the enqueue path deduplicate repeated clicks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: The workflow types the queue knows about. Each maps to a registered worker
#: handler. Kept in sync with the ``WORKFLOW_TYPE`` constants in the service
#: modules so an enqueued job and the persisted ``AgentRun.workflow_type``
#: agree.
WorkflowType = Literal[
    "smoke",  # foundation-only no-op handler used by queue runtime tests
    "jd_paste_parsing",
    "resume_fact_extraction",
    "resume_aware_jd_analysis",
    "readiness_generation",
]


class WorkflowPayload(BaseModel):
    """Base envelope for every queued workflow job.

    Fields:

    - ``workflow_type`` — selects the worker handler.
    - ``user_id`` — the owning user; the worker re-checks ownership when it
      re-loads resources so a cross-user payload cannot exfiltrate data.
    - ``agent_run_id`` — the durable ``AgentRun`` row created before enqueue.
      The worker flips its status (queued → running → succeeded/failed) so
      PostgreSQL stays the source of truth, never Redis.
    - ``idempotency_key`` — stable key for deduplicating repeated submissions.
    """

    model_config = ConfigDict(frozen=True)

    workflow_type: WorkflowType
    user_id: str
    agent_run_id: str
    idempotency_key: str = Field(
        description="Stable key used to deduplicate repeated enqueue attempts."
    )


class SmokePayload(WorkflowPayload):
    """No-op payload for the foundation smoke handler.

    The smoke handler marks its ``AgentRun`` succeeded without touching any
    model or domain resource. It exists to prove the queue wiring (enqueue →
    worker → DB status flip) end to end in tests, before real workflows plug
    in.
    """

    workflow_type: Literal["smoke"] = "smoke"


class JdPasteParsePayload(WorkflowPayload):
    """Payload for the JD paste parsing workflow.

    Carries the raw JD text because — unlike resume fact extraction, which can
    re-read ``ResumeVersion.raw_text`` — a JD paste parse has no durable parent
    row at enqueue time (the ``JobPosting`` is created *after* parsing
    succeeds). The raw text lives only in the Redis queue entry, which arq
    expires automatically; it is never persisted to PostgreSQL. The worker
    handler passes it to the service orchestrator, which sanitizes all step
    and run metadata so only ``raw_jd_len`` (not the text) is stored on
    ``AgentRun`` / ``AgentStep`` rows.
    """

    workflow_type: Literal["jd_paste_parsing"] = "jd_paste_parsing"

    raw_jd: str
    platform: str | None = None


class ResumeFactExtractionPayload(WorkflowPayload):
    """Payload for the resume fact extraction workflow.

    References the durable ``Resume`` + ``ResumeVersion`` rows by ID — the
    worker re-reads ``raw_text`` from the version inside its own session, so
    no raw resume content crosses the queue boundary (contrast
    :class:`JdPasteParsePayload` which must carry ``raw_jd`` because no parent
    row exists at parse time). This keeps a leaked queue entry from exposing
    user resume content.

    The ``AgentRun`` (``workflow_type="resume_fact_extraction"``) is created
    in the ``queued`` state by the API before enqueue; the worker flips it to
    ``running`` → ``succeeded``/``failed`` and writes typed ``facts`` +
    ``_extraction`` status into ``ResumeVersion.parsed_facts``.
    """

    workflow_type: Literal["resume_fact_extraction"] = "resume_fact_extraction"

    resume_id: str
    version_id: str


class ResumeAwareJdAnalysisPayload(WorkflowPayload):
    """Payload for the resume-aware JD analysis workflow.

    References the durable ``JobPosting`` + ``ResumeVersion`` rows by ID — the
    worker re-reads them from its own DB session, so no raw JD or resume
    content crosses the queue boundary (same reference-by-ID approach as
    :class:`ResumeFactExtractionPayload`). This keeps a leaked queue entry
    from exposing user content.

    The ``AgentRun`` (``workflow_type="resume_aware_jd_analysis"``) is created
    in the ``queued`` state by the API before enqueue and linked to the job
    via ``AgentRun.job_id``; the worker flips it to ``running`` →
    ``succeeded``/``failed``, persisting ``JobAnalysis`` +
    ``GeneratedArtifact`` only on success.
    """

    workflow_type: Literal["resume_aware_jd_analysis"] = "resume_aware_jd_analysis"

    job_id: str
    resume_version_id: str


class ReadinessGenerationPayload(WorkflowPayload):
    """Payload for the readiness artifact generation workflow.

    References the durable ``ApplicationRecord`` + ``JobPosting`` +
    ``ResumeVersion`` rows by ID — the worker re-reads them from its own DB
    session, so no raw JD or resume content crosses the queue boundary. The
    ``artifact_type`` selects which of the four readiness artifacts to
    generate. The ``source_hash`` captured at enqueue time lets the worker
    detect stale sources (the snapshot changed between enqueue and execution)
    and refuse to persist a potentially-mismatched artifact.

    The ``AgentRun`` (``workflow_type="readiness_generation"``) is created in
    the ``queued`` state by the API before enqueue and linked to the job via
    ``AgentRun.job_id``; the worker flips it to ``running`` →
    ``succeeded``/``failed``, persisting a ``GeneratedArtifact`` only on
    success.
    """

    workflow_type: Literal["readiness_generation"] = "readiness_generation"

    application_id: str
    job_id: str
    resume_version_id: str
    artifact_type: str
    source_hash: str
