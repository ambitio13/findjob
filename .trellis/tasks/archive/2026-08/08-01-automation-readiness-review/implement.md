# Implementation Plan

## Step 1 — Gather Evidence

- Run backend/frontend checks.
- Inspect application state/failure tests.
- Inspect approval guard tests.
- Review sample application timelines.

## Step 2 — Fill Review Checklist

- Mark each go criterion pass/fail.
- Record exact evidence path or command result.
- List residual risks.

## Step 3 — Decide

- If go: draft first platform pilot task.
- If no-go: draft hardening tasks.

## Step 4 — Platform Pilot PRD If Go

The first pilot PRD must include:

- target platform;
- dry-run scope;
- login/session handling;
- approval checkpoint;
- external action idempotency;
- failure matrix;
- rollback/manual reconciliation path.

## Validation

```bash
python3 ./.trellis/scripts/task.py validate 08-01-automation-readiness-review
git diff --check
```

