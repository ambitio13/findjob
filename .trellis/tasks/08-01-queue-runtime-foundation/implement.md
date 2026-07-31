# Implementation Plan

1. Add queue dependency and lockfile update.
2. Extend settings for queue namespace, job timeout, max retries.
3. Create `app.queue.payloads` with typed base payload and smoke payload.
4. Create `app.queue.runtime` with Redis pool/client and enqueue helper.
5. Create `app.queue.worker` and a smoke handler.
6. Add Docker compose worker service and backend docs/env example updates.
7. Add tests for config, enqueue, direct handler execution, and failed handler state.
8. Run backend/frontend quality gates.

## Validation

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
python3 .trellis/scripts/task.py validate 08-01-queue-runtime-foundation
git diff --check
```
