# Implementation Plan

## Phase Order

1. `08-01-queue-runtime-foundation`
   - Add queue dependency/config.
   - Add enqueue/runtime abstractions.
   - Add worker process entrypoint.
   - Add docker-compose worker service.
   - Add tests for enqueue and worker failure marking.

2. `08-01-async-jd-paste-parsing`
   - Submit JD parse job immediately.
   - Persist queued/running/succeeded/failed `AgentRun`.
   - Store parsed draft result for polling/hydration.
   - Update modal to show visible progress instead of blocking.

3. `08-01-async-resume-fact-extraction`
   - Replace FastAPI `BackgroundTasks` upload runner with queue enqueue.
   - Convert re-extract to enqueue-and-poll.
   - Keep upload returning saved resume immediately.
   - Preserve `pending/running/succeeded/failed/not_run` status semantics.

4. `08-01-async-jd-analysis-workflow`
   - Convert JD analysis run endpoint to submit-and-poll.
   - Worker persists `JobAnalysis` and `GeneratedArtifact` on success.
   - Failed runs remain visible by job filter.
   - Job detail hydrates result when terminal success appears.

5. `08-01-async-job-ux-observability`
   - Consolidate polling hooks/components.
   - Normalize status tags, retry copy, and progress panels.
   - Remove any duplicated waiting UX left by earlier phases.

## Global Validation Commands

Run after each subtask:

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate <task-name>
git diff --check
```

Run before closing the parent:

```bash
rg -n "await .*parse_jd|await .*run_resume_aware_jd_analysis|BackgroundTasks|await .*extract_resume_facts" backend/app frontend/src
python3 .trellis/scripts/task.py list
```

The final `rg` should show no production user-facing synchronous model waits except worker handlers or tests.

## Risk Points

- Contract changes will affect frontend types and tests at the same time.
- `AgentRun.status` may need schema/model enum widening; migrate carefully if constraints exist.
- Worker must not import API dependencies that yield request-scoped resources.
- Duplicate clicks can create duplicate jobs unless submit endpoints use idempotency keys or frontend disables while queued.
- TestClient behavior differs from real background execution; worker tests should exercise handlers directly and queue adapter boundaries.

## Handoff Notes

- Start with the foundation task. Do not migrate individual workflows until the queue runtime is merged.
- Each workflow task should be independently shippable and should leave the app usable.
- Prefer small compatibility adapters over broad rewrites of existing service orchestration.
- Preserve all current audit and no-leak tests, then add queue-specific tests.
