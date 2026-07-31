# Implementation Plan

1. Add resume fact extraction queue payload.
2. Refactor service orchestration so worker can run with a fresh session and durable run.
3. Replace upload `BackgroundTasks` scheduling with queue enqueue.
4. Convert explicit re-extract endpoint to enqueue-and-return.
5. Update frontend types/copy for any new status.
6. Update tests around upload/re-extract from background task semantics to queue/worker semantics.
7. Run backend/frontend quality gates.

## Validation

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate 08-01-async-resume-fact-extraction
git diff --check
```
