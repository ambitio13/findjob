# Implementation Plan

## Step 1 — Types

- Add status enum and failure envelope Pydantic models.
- Add next-action enum.
- Add source snapshot model.

## Step 2 — State Service

- Implement transition table.
- Implement `assert_transition`.
- Add stable errors for invalid transitions.

## Step 3 — Source Hash

- Build source snapshot from job, resume version, user profile, and prompt
  versions.
- Hash metadata only, not raw text.

## Step 4 — Duplicate Active Operation Contract

- Define helper for active operation keys.
- Decide whether caller should return existing run or raise 409.
- Document this in service docstrings.

## Step 5 — Tests

- Good transitions pass.
- Bad transitions fail.
- Failure envelope validates required fields.
- Source hash changes on job/profile/prompt changes.
- Source hash does not contain raw resume or JD text.

## Validation

```bash
cd backend && pytest app/tests/test_application_state.py
cd backend && ruff check .
```

