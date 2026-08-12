"""Pydantic schemas for the BOSS recommended-job discovery workflow.

The discovery pipeline traverses the user's BOSS recommended-job list page
(`/web/geek/jobs`) with **zero navigation**: it scans visible cards, clicks card
roots (never title links), reads the in-place detail pane, upserts each job,
and runs match → prepare — all in one serial pass.

Phase 1 is ``prepare_only``: the pipeline stops at ``approval_required`` or
``needs_review`` and never auto-approves or auto-executes. ``auto_execute`` is
rejected at the API layer via the dry-run gate.

Safety invariants (mirrors ``evolution-contracts.md`` §1, §14):

- The discovery pipeline never auto-approves or auto-executes.
- Cross-user access returns 404 (not 403).
- Serial item processing only — no concurrent userscript-bridge instructions.
- ``needs_review`` / ``skipped`` (including ``already_persisted``) are item
  outcomes, not incidents — they reset the consecutive-failure counter.
- ``skip_reason`` is business metadata, not ``failure_code``.

Per-item progress is stored in ``AgentRun.result`` as structured JSON so no
new table is needed (same pattern as ``boss_batch_loop_service``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class DiscoveryRunStatus(StrEnum):
    """Top-level status of a discovery run.

    - ``running`` — the pipeline is actively processing items.
    - ``paused`` — crash-recovery only in v0.1; a stuck non-terminal run can be
      marked paused for later resume.
    - ``completed`` — all items reached a terminal state.
    - ``hard_stopped`` — the consecutive-failure threshold was hit.
    - ``failed`` — an unrecoverable error stopped the run before items
      (e.g. bridge disconnected, wrong page).
    """

    running = "running"
    paused = "paused"
    completed = "completed"
    hard_stopped = "hard_stopped"
    failed = "failed"


class DiscoveryItemStatus(StrEnum):
    """Per-item status within a discovery run.

    Terminal states (the pipeline moves to the next item):

    - ``prepared`` — read + upsert + match + prepare all succeeded; an
      ``approval_required`` action exists, awaiting human approval.
    - ``needs_review`` — the match decision was ``needs_review``. Not an incident.
    - ``skipped`` — the match decision was ``skip`` or the job+resume already
      has a reusable communicate action (``skip_reason=already_persisted``).
      Not an incident.
    - ``failed`` — an exception occurred. Counts toward the consecutive-failure
      hard-stop.
    - ``stopped`` — the run was hard-stopped before this item was processed.

    Non-terminal:

    - ``pending`` — the item has been scanned but not yet processed.
    - ``opening`` — ``open_job_by_key`` was dispatched.
    - ``reading`` — waiting for the pane and reading the JD.
    - ``persisted`` — job upserted, about to match.
    - ``matching`` — match decision in progress.
    """

    pending = "pending"
    opening = "opening"
    reading = "reading"
    persisted = "persisted"
    matching = "matching"
    prepared = "prepared"
    needs_review = "needs_review"
    skipped = "skipped"
    failed = "failed"
    stopped = "stopped"


class DiscoveryRequest(BaseModel):
    """Body of ``POST /boss/recommended-jobs/discovery``.

    ``resume_version_id`` scopes all match/prepare calls to this resume
    version. ``limit`` caps the number of candidates scanned and processed;
    default 3, hard cap 10 (sync execution budget). ``mode`` selects
    ``prepare_only`` (Phase 1) or ``auto_execute`` (Phase 2, rejected by the
    API layer until the dry-run gate passes).
    """

    resume_version_id: str = Field(min_length=1)
    limit: int = Field(default=3, ge=1, le=10)
    mode: Literal["prepare_only", "auto_execute"] = "prepare_only"


class DiscoveryItemOut(BaseSchema):
    """One item's progress within a discovery run."""

    job_key: str
    rank: int
    status: DiscoveryItemStatus
    title: str | None = None
    company: str | None = None
    job_id: str | None = None
    application_id: str | None = None
    decision: str | None = None
    score: float | None = None
    match_artifact_id: str | None = None
    action_id: str | None = None
    failure_code: str | None = None
    skip_reason: str | None = None
    message: str | None = None


class DiscoveryStatusOut(BaseSchema):
    """Response for the discovery start/status/resume endpoints.

    ``consecutive_failures`` is the current run of ``failed`` items; when it
    reaches ``hard_stop_threshold`` the run transitions to ``hard_stopped``
    and all remaining items are set to ``stopped``.
    """

    run_id: str
    status: DiscoveryRunStatus
    mode: Literal["prepare_only", "auto_execute"]
    resume_version_id: str
    total: int
    processed: int
    consecutive_failures: int
    hard_stop_threshold: int
    items: list[DiscoveryItemOut]
    message: str | None = None


class DiscoveryPauseOut(BaseSchema):
    """Response for the pause/resume endpoints (lightweight status)."""

    run_id: str
    status: DiscoveryRunStatus
    message: str


__all__ = [
    "DiscoveryItemOut",
    "DiscoveryItemStatus",
    "DiscoveryPauseOut",
    "DiscoveryRequest",
    "DiscoveryRunStatus",
    "DiscoveryStatusOut",
]
