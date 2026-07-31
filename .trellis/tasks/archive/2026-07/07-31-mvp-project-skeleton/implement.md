# MVP Project Skeleton Implementation Plan

## Pre-Development

- Read `AGENTS.md`.
- Read `.trellis/workflow.md`.
- Read `.trellis/spec/README.md`.
- Read every file listed in `implement.jsonl`.
- Keep the task in MVP skeleton scope.

## Steps

1. Create repository skeleton.
   - Add `backend/`, `frontend/`, and root Docker/local docs.
   - Add `.env.example` files without secrets.

2. Build backend foundation.
   - Initialize FastAPI project.
   - Add health endpoint and versioned router.
   - Add config/settings.
   - Add structured logging setup.
   - Add PostgreSQL session setup.
   - Add Redis client setup.
   - Add backend test harness and health test.

3. Add data model foundation.
   - Choose and document ORM/migration tool.
   - Add initial models/schemas for entities listed in `design.md`.
   - Add migration or migration-ready setup.

4. Add model gateway.
   - Define provider-neutral request/response schemas.
   - Add fake provider for tests.
   - Add DeepSeek OpenAI-compatible provider behind config.
   - Ensure no direct provider calls outside the gateway.

5. Add lightweight agent runtime foundation.
   - Add `AgentRun`, `AgentStep`, `ToolCall`, and `Artifact` code boundaries.
   - Add planner/executor/reflector interfaces or minimal classes.
   - Add tool registry skeleton.
   - Add one deterministic smoke workflow.

6. Build frontend foundation.
   - Initialize React + Ant Design Pro-compatible project.
   - Add app shell/navigation.
   - Add jobs table placeholder.
   - Add manual JD entry UI.
   - Add job detail placeholder.
   - Add agent run status and generated artifact placeholders.
   - Add API health/status integration.

7. Wire Docker Compose and documentation.
   - Compose PostgreSQL, Redis, backend, and frontend.
   - Document startup, tests, and environment variables.
   - Confirm local commands from a clean shell.

8. Validate.
   - Run backend tests.
   - Run frontend lint/type/build checks.
   - Run Docker startup or the closest available smoke check.
   - Search for forbidden scope creep: platform automation, automatic
     submission, complete resume export.

## Validation Commands

Update these commands if chosen tooling differs:

```bash
cd backend && pytest
cd backend && ruff check .
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
docker compose up --build
```

## Review Gates

- Backend starts and health endpoint works.
- Frontend starts and displays backend status.
- Model gateway has fake provider and DeepSeek provider boundaries.
- Agent runtime has durable concepts even if persistence implementation is
  minimal.
- Scope does not include platform automation or complete resume export.

## Rollback Points

- If frontend tooling becomes too heavy, keep a minimal React app and document
  the Ant Design Pro integration gap.
- If ORM/migration setup stalls, keep typed models and schema docs but do not
  fake a working migration.
- If Docker startup fails due local environment, keep backend/frontend direct
  startup commands working and document the blocker.

