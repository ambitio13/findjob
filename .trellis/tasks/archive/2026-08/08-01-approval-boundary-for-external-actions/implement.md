# Implementation Plan

## Step 1 — Action Types

- Add action/approval schemas.
- Add payload hash helper.
- Add approval guard helper.

## Step 2 — Persistence

- Add normalized table or JSON-backed repository.
- Append timeline events for preview/approve/revoke/stale.

## Step 3 — API

- Preview action payload.
- Approve exact payload.
- Revoke approval.
- Read action detail.

## Step 4 — UI

- Show preview panel.
- Show exact outgoing message/materials.
- Show approve/revoke/stale states.

## Step 5 — Tests

- Approve exact payload.
- Block missing approval.
- Block changed payload hash.
- Block stale source.
- Revoke approval.
- Ensure no external execution path exists.

## Validation

```bash
cd backend && pytest app/tests/test_application_actions.py
cd backend && ruff check .
cd frontend && pnpm type-check
```

