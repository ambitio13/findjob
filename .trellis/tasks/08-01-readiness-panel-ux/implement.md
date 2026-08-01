# Implementation Plan

## Step 1 — Types and Client

- Add frontend types for ApplicationRecord, timeline, failure envelope,
  readiness artifacts.
- Add API client functions.

## Step 2 — Pages

- Add application list/detail routes.
- Add compact job-detail entry point.

## Step 3 — Readiness Components

- Build summary, artifact checklist, failure panel, timeline panel.
- Reuse existing AgentRun status components/hooks where possible.

## Step 4 — Actions

- Generate/retry artifacts.
- Pause/resume.
- Manual status update.
- Mark manually submitted.

## Step 5 — Frontend Checks

- Type-check expected response shapes.
- Test key rendering helpers if test framework exists.
- Manual smoke with backend running.

## Validation

```bash
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
```

