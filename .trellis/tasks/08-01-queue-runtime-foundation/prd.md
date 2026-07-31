# Queue Runtime Foundation

## Goal

Introduce a Redis-backed async queue runtime and worker process that all model-backed workflows can use without blocking FastAPI request handlers.

## Requirements

- Add an async-native queue library, recommended `arq`.
- Add queue settings under `Settings`: Redis URL reuse, queue namespace, default timeout, retry count.
- Add typed queue payload models for workflow jobs.
- Add enqueue helpers that API routes can call without importing provider-specific queue details.
- Add a worker entrypoint that opens fresh DB sessions and constructs its own model gateway.
- Add Docker/local dev support for running the worker.
- Add a minimal health/diagnostic path or test helper to prove queue connectivity.
- Keep PostgreSQL as durable source of truth; Redis must not be the only place where run state exists.

## Acceptance Criteria

- [ ] Backend dependencies include the selected queue runtime.
- [ ] `backend/app/queue/` contains runtime, payload, and worker modules.
- [ ] A no-op/smoke queued job can be enqueued and processed in tests.
- [ ] Worker functions never accept request-scoped SQLAlchemy sessions or request DI gateway instances.
- [ ] Docker compose includes a worker service using the same image/config as backend.
- [ ] Local README or backend docs include the worker command.
- [ ] Tests cover enqueue success, handler success, handler exception marking a run failed, and Redis unavailable behavior.

## Out Of Scope

- Migrating JD/resume workflows. This task only builds the queue substrate.
- WebSocket push.
- Admin-wide queue dashboard.
