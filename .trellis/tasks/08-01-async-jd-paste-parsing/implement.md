# Implementation Plan

1. Add JD parse queue payload.
2. Refactor `jd_parse_service.parse_jd` so worker can use an existing `AgentRun`.
3. Change `POST /jobs/parse` to create queued run + enqueue payload + return immediately.
4. Add polling/result helper in API client if needed.
5. Update `JobCreateModal` UX: submit, poll, hydrate, retry.
6. Update tests from synchronous parse result to queued result plus worker execution.
7. Run backend/frontend quality gates.

## Validation

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate 08-01-async-jd-paste-parsing
git diff --check
```
