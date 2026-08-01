# Async Queue Architecture Upgrade

## Goal

Upgrade every model-backed or long-running user workflow to a queue-backed asynchronous execution model so HTTP requests return quickly, users see explicit progress, failures are auditable, and results hydrate into the UI without manual refresh guesswork.

## Background

The resume upload timeout fix proved the product direction: users should never confuse a slow model call with a failed primary action. The current codebase still has several synchronous waits:

- `POST /api/v1/jobs/parse` waits for model-backed JD paste parsing before returning.
- `POST /api/v1/jobs/{job_id}/analyses` waits for resume-aware JD analysis before returning.
- `POST /api/v1/resumes/{resume_id}/versions/{version_id}/extract` waits for model-backed resume fact extraction.
- `POST /api/v1/resumes` now uses FastAPI `BackgroundTasks`; this avoids the browser timeout but still ties long model work to the web process and should be moved to the same worker queue.

The repository already has Redis configured (`REDIS_URL`, docker-compose redis service, `app.cache.redis`) and all model-backed workflows already persist `AgentRun` / `AgentStep` audit trails. The queue should build on those facts instead of introducing a second source of truth.

## Product Requirements

- All user-triggered model workflows must respond quickly with a durable run/task reference, not wait for the model call to finish.
- Users must see clear lifecycle states: queued, running, succeeded, failed, and not_run where applicable.
- Users must be able to leave and return to the relevant page and still see the latest run state and result.
- Failed runs must remain visible with sanitized step metadata and retry affordances.
- The UI must avoid indefinite spinners. Actions should become "submitted/running" states with visible progress panels.
- Queue execution must preserve current user ownership boundaries and 404-on-cross-user behavior.
- PostgreSQL remains the source of truth for durable business state and audit state. Redis is transport/runtime coordination only.

## Technical Requirements

- Introduce a single backend queue runtime backed by Redis and a separate worker process.
- Recommended queue library: `arq`, because the existing services and model gateway are async-native. Celery is heavier than the MVP needs; RQ is simpler but sync-first.
- Define an internal job envelope that records `workflow_type`, `user_id`, resource IDs, `agent_run_id`, retry metadata, and idempotency key.
- Create/update `AgentRun` before enqueue where the frontend needs an immediate visible run.
- Worker jobs must open their own DB session and construct their own model gateway/settings inside the worker process.
- Worker code must never receive request-scoped SQLAlchemy sessions or request-scoped dependency objects.
- Enqueue APIs must be idempotent enough to avoid double-submission from repeated clicks.
- Preserve existing sanitized step-result conventions. Do not persist raw JD text or raw resume content inside `AgentRun.result` / `AgentStep.result`.
- Keep compatibility shims only where needed during migration; remove or deprecate synchronous user-facing endpoints by the end of the parent task.

## Subtasks

1. `08-01-queue-runtime-foundation` — queue runtime, worker process, shared contracts, deployment/test infrastructure.
2. `08-01-async-jd-paste-parsing` — async JD paste parsing, draft result hydration, non-blocking create-job flow.
3. `08-01-async-resume-fact-extraction` — replace upload `BackgroundTasks` and synchronous re-extract with queued resume fact extraction.
4. `08-01-async-jd-analysis-workflow` — async resume-aware JD analysis, immediate run creation, result polling/hydration.
5. `08-01-async-job-ux-observability` — shared frontend async UX, polling hooks, progress surfaces, retry states, and copy.

## Acceptance Criteria

- [ ] No production user-facing route waits synchronously for a model call.
- [ ] Redis-backed worker runs model workflows outside the FastAPI request lifecycle.
- [ ] Each queued workflow has a persisted `AgentRun` visible immediately after submission.
- [ ] Each workflow exposes enough API state for the frontend to poll and render queued/running/succeeded/failed.
- [ ] JD paste parsing no longer blocks the modal on model completion.
- [ ] Resume upload and re-extract use the queue, not FastAPI `BackgroundTasks`.
- [ ] JD analysis submission returns immediately and the job detail page hydrates the final analysis when ready.
- [ ] Failed queued jobs are auditable through `AgentRun` and can be retried from the relevant UI.
- [ ] Tests cover enqueue, worker success, worker failure, retry, cross-user access, and no raw prompt/resume leakage.
- [ ] Docker/local development includes a worker service or documented worker command.

## Out Of Scope

- Full distributed scheduler or cron system.
- Multi-tenant authentication beyond the current `X-User-Id` boundary.
- WebSocket/SSE push. Polling is acceptable for this phase, but the API contracts should not block adding push later.
- A full admin dashboard for all users' queued jobs.
