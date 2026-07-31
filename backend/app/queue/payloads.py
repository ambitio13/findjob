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
