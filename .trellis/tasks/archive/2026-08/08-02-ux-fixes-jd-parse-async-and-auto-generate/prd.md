# UX Fixes — JD Parse Async + Analysis Status + Auto-Generate Readiness

## Goal

Fix three UX issues surfaced during pilot testing of the job/application flow:

1. **JD paste modal blocks during parse** — the create modal stays open
   polling until the parse run completes, which feels synchronous.
2. **JD analysis initial status shows "failed"** — queued/running analysis
   runs (which have not yet produced a `JobAnalysis` detail row) render as
   "运行失败" instead of "运行中".
3. **Readiness artifacts require manual click-per-type** — creating an
   application record should auto-generate all four readiness artifacts (HR
   opening message, resume rewrite snippet, skill gap plan, interview prep)
   so the user can self-serve.

## Requirements

### Fix 1 — JD paste async (create-job-first)

- `POST /jobs/parse` creates a `JobPosting` up front (placeholder
  company/title `"(解析中…)"`, `jd_raw` persisted) **before** enqueuing the
  parse run, so the job appears in the list immediately.
- The `AgentRun` is linked to the job via `job_id`.
- The worker writes parsed fields back to `job.jd_normalized` and overwrites
  the placeholder company/title/location/salary/direction on success.
- `PATCH /jobs/{id}` lets the user correct fields (parsed draft or
  placeholder) after the run completes (or fails).
- The frontend create modal closes immediately on submit; `JobsTable` shows a
  parse-status column + auto-refreshes while a parse run is active;
  `JobDetailPage` supports inline editing.

### Fix 2 — JD analysis status display (frontend only)

- `JobDetailPage.AnalysisSection` must distinguish queued/running runs from
  failed runs. Pending runs show "运行中" + `AgentRunStatusTag`; only
  `status === "failed"` shows "运行失败".
- The previous `!v.detail` heuristic caused pending runs (no `JobAnalysis`
  row yet) to render as failed.

### Fix 3 — Auto-generate readiness artifacts on application create

- `create_application` returns `(record, is_new)`; the API surfaces
  `is_duplicate` on `ApplicationOut` so the frontend can distinguish a fresh
  create from a returned duplicate.
- `ArtifactChecklist` auto-triggers all four artifact generations on mount
  when: `status === "planned"`, `artifacts.length === 0`, resume is bound,
  `!is_duplicate`, and no active run is already in flight.
- Multi-run polling: `pollRunIds: Map<ReadinessArtifactType, string>` tracks
  concurrent runs; the existing `useAgentRunPolling` polls the first active
  runId and refreshes artifacts on each tick so completed types appear while
  others continue.
- 409 (duplicate-active-run) is silently swallowed during auto-generation.

## Acceptance Criteria

- [x] `POST /jobs/parse` returns `JdParseSubmitResponse` with a `job` field;
      a `JobPosting` row exists with placeholder company/title and `jd_raw`.
- [x] Worker success writes `jd_normalized` + overwrites placeholder
      company/title; worker failure leaves the placeholder intact.
- [x] `PATCH /jobs/{id}` updates editable fields; cross-user returns 404.
- [x] `JobCreateModal` closes immediately after parse submit.
- [x] `JobsTable` shows parse-status tags and auto-refreshes while parsing.
- [x] `JobDetailPage` shows "运行中" (not "失败") for queued/running analyses.
- [x] `JobDetailPage` supports inline editing of job fields via PATCH.
- [x] `ApplicationOut` carries `is_duplicate`; duplicate create sets it true.
- [x] `ArtifactChecklist` auto-generates all 4 artifacts on a fresh, planned,
      resume-bound application; skips duplicates.
- [x] Backend: `uv run pytest` — 473 passed.
- [x] Frontend: `pnpm lint && pnpm type-check && pnpm build` — all green.

## Notes

- Sanitization contract preserved: no raw JD/resume text in `AgentRun` /
  `AgentStep` metadata, only lengths. The raw JD text lives on the
  `JobPosting` row (durable, user-editable) and in the transient queue
  payload (arq expires it automatically).
- The create-job-first pattern mirrors the resume-upload flow: create the
  row immediately, parse async, let the list refresh show progress.
