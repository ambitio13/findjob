# Async JD Paste Parsing

## Goal

Move JD paste parsing from a blocking modal action to a queued workflow that gives users immediate feedback, visible progress, and automatic draft hydration when parsing completes.

## Current Evidence

- `frontend/src/features/jobs/JobCreateModal.tsx` awaits `parseJobJd(rawJd)` before the user can continue.
- `backend/app/api/v1/jobs.py` awaits `parse_jd(...)` in `POST /jobs/parse`.
- `backend/app/services/jd_parse_service.py` already creates `AgentRun` and `AgentStep` records, so it can be adapted to worker execution.

## Requirements

- Submitting raw JD text returns immediately with an `AgentRun` in `queued` state.
- The modal shows queued/running/succeeded/failed instead of a blocking spinner.
- On success, parsed fields hydrate into the create-job form automatically.
- On failure, the user can still manually fill the form and can retry parsing.
- Raw JD text must not be persisted in `AgentRun.result` or `AgentStep.result`.
- Cross-user run access remains blocked.

## Acceptance Criteria

- [ ] `POST /api/v1/jobs/parse` no longer awaits a model call.
- [ ] The response includes an immediate `agent_run` and enough metadata for polling.
- [ ] Worker executes the existing JD parse orchestration and persists sanitized steps.
- [ ] Frontend modal shows progress and hydrates parsed draft on terminal success.
- [ ] Failed parse shows a retry action and preserves the raw JD input locally.
- [ ] Tests cover enqueue response, worker success, worker failure, retry, and no raw JD leakage.

## Dependencies

- Depends on `08-01-queue-runtime-foundation`.
