"""Pydantic schemas for the BOSS recommended-job batch-loop workflow.

The batch loop extends the single-job pilot (inspect → match → prepare) to a
serial traversal of the user's BOSS recommended-job list. Phase 1 is
``prepare_only``: the loop stops at ``approval_required`` or ``needs_review``
and never auto-approves or auto-executes. Phase 2 (``auto_execute`` mode) is
rejected by the backend until the dry-run gate passes.

Safety invariants (mirrors ``evolution-contracts.md`` §1):

- The batch loop never auto-approves or auto-executes. Every external side
  effect still goes through the existing approval + idempotency guard.
- Cross-user access returns 404 (not 403).
- Serial execution only — no concurrent userscript-bridge instructions.
- ``skip`` / ``needs_review`` are item outcomes, not incidents — they do not
  count toward the consecutive-failure hard-stop.

Per-item progress is stored in ``AgentRun.result`` as structured JSON so no
new table is needed (``design.md`` §Persistence).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class BatchRunStatus(StrEnum):
    """Top-level status of a batch run.

    - ``running`` — the loop is actively processing items.
    - ``paused`` — the user paused the loop; it can be resumed.
    - ``completed`` — all items reached a terminal state.
    - ``hard_stopped`` — the consecutive-failure threshold was hit.
    - ``failed`` — an unexpected error stopped the loop before any item.
    """

    running = "running"
    paused = "paused"
    completed = "completed"
    hard_stopped = "hard_stopped"
    failed = "failed"


class BatchItemStatus(StrEnum):
    """Per-item status within a batch run.

    Terminal states (the loop moves to the next item):

    - ``prepared`` — inspect + match + prepare all succeeded; an
      ``approval_required`` action exists, awaiting human approval.
    - ``needs_review`` — the match decision was ``needs_review``; the human
      review UI should be shown. Not an incident.
    - ``skipped`` — the match decision was ``skip``. Not an incident.
    - ``failed`` — an exception occurred during inspect/match/prepare. Counts
      toward the consecutive-failure hard-stop.
    - ``stopped`` — the loop was hard-stopped before this item was processed.

    Non-terminal:

    - ``pending`` — the item has not been processed yet.
    - ``inspecting`` — the item is currently being processed.
    """

    pending = "pending"
    inspecting = "inspecting"
    prepared = "prepared"
    needs_review = "needs_review"
    skipped = "skipped"
    failed = "failed"
    stopped = "stopped"


class BatchLoopRequest(BaseModel):
    """Body of ``POST /boss/recommended-jobs/batch-loop``.

    ``resume_version_id`` scopes all match/prepare calls to this resume
    version. ``job_ids`` is the ordered list of job IDs to process (the
    frontend collects these from previously-inspected jobs). ``limit`` caps
    the number of items processed in this invocation; the remaining items
    stay ``pending`` and can be resumed later. ``mode`` selects
    ``prepare_only`` (Phase 1) or ``auto_execute`` (Phase 2, gated).
    """

    resume_version_id: str = Field(min_length=1)
    job_ids: list[str] = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    mode: Literal["prepare_only", "auto_execute"] = "prepare_only"


class BatchLoopItemOut(BaseSchema):
    """One item's progress within a batch run."""

    job_id: str
    status: BatchItemStatus
    decision: str | None = None
    score: float | None = None
    match_artifact_id: str | None = None
    action_id: str | None = None
    application_id: str | None = None
    error: str | None = None


class BatchLoopStatusOut(BaseSchema):
    """Response for the batch-loop status/pause/resume endpoints.

    ``consecutive_failures`` is the current run of ``failed`` items; when it
    reaches ``hard_stop_threshold`` the run transitions to ``hard_stopped``
    and all remaining items are set to ``stopped``.
    """

    run_id: str
    status: BatchRunStatus
    mode: Literal["prepare_only", "auto_execute"]
    resume_version_id: str
    total: int
    processed: int
    consecutive_failures: int
    hard_stop_threshold: int
    items: list[BatchLoopItemOut]
    message: str | None = None


class BatchLoopPauseOut(BaseSchema):
    """Response for the pause/resume endpoints (lightweight status)."""

    run_id: str
    status: BatchRunStatus
    message: str


__all__ = [
    "BatchItemStatus",
    "BatchLoopItemOut",
    "BatchLoopPauseOut",
    "BatchLoopRequest",
    "BatchLoopStatusOut",
    "BatchRunStatus",
]
