"""Pydantic schemas for the BOSS immediate-communicate workflow.

The communicate flow has two phases exposed via the API:

1. **Prepare** — read a previously persisted ``boss_match_decision`` artifact
   whose decision is ``communicate``, extract its ``opening_message``, and draft
   a ``boss_immediate_communicate`` :class:`ApplicationAction` in
   ``approval_required`` status bound to a ``payload_hash`` + external
   idempotency key.
2. **Execute** — run the approval + idempotency guards and (in a later subtask)
   invoke the browser adapter to click "立即沟通" and send the opening message.

These schemas are the request/response contracts for the two endpoints. The
durable action representation lives in
:class:`~app.schemas.application_action.ApplicationActionOut`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.application_action import ApplicationActionOut
from app.schemas.common import BaseSchema


class CommunicatePrepareRequest(BaseModel):
    """Body of ``POST /boss/recommended-jobs/{job_id}/communicate/prepare``."""

    resume_version_id: str = Field(min_length=1)
    match_artifact_id: str = Field(min_length=1)


class CommunicatePrepareOut(BaseSchema):
    """Response for the prepare endpoint.

    ``message`` carries a human-readable explanation of the action state (e.g.
    "communication action drafted, approval required").
    """

    action: ApplicationActionOut
    message: str


class CommunicateExecuteRequest(BaseModel):
    """Body of ``POST /boss/recommended-jobs/{job_id}/communicate/{action_id}/execute``.

    ``application_id`` is required so the execute endpoint can scope the action
    to the correct application (the action row already carries it, but we
    require it explicitly so the request is self-describing and the ownership
    check is explicit).
    """

    application_id: str = Field(min_length=1)


class CommunicateExecuteOut(BaseSchema):
    """Response for the execute endpoint.

    ``message`` indicates whether the guards passed (Subtask 5) or the adapter
    was invoked (Subtask 6+).
    """

    action: ApplicationActionOut
    message: str


__all__ = [
    "CommunicateExecuteOut",
    "CommunicateExecuteRequest",
    "CommunicatePrepareOut",
    "CommunicatePrepareRequest",
]
