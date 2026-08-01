# Implementation Plan

1. Add JD analysis queue payload.
2. Refactor JD analysis service so worker can execute with an existing queued run.
3. Change analysis route to enqueue and return immediately.
4. Update schemas/client types for submit response.
5. Update JobDetailPage to poll active runs and hydrate results.
6. Add duplicate active-run guard.
7. Update tests for queued submit, worker success/failure, and frontend type/build.
8. Run quality gates.

## Validation

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate 08-01-async-jd-analysis-workflow
git diff --check
```
