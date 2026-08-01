# Implementation Plan

1. Inventory duplicated polling/status code after workflow migrations.
2. Extract shared status constants and display helpers.
3. Extract polling hook with terminal-state handling and cleanup.
4. Apply to JD paste modal, resume detail, and job detail.
5. Normalize retry/failure copy.
6. Run frontend quality gates and targeted backend tests if contracts changed.

## Validation

```bash
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
cd backend && .venv/bin/pytest -q
python3 .trellis/scripts/task.py validate 08-01-async-job-ux-observability
git diff --check
```
