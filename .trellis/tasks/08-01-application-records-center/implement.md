# Implementation Plan

## Step 1 — Schema and Migration

- Extend `ApplicationRecord` as needed.
- Add Pydantic schemas for create/list/detail/status update.
- Add Alembic migration.

## Step 2 — Repository

- `create_for_user`
- `get_for_user`
- `list_for_user`
- `update_status_with_event`
- duplicate lookup by job/resume/user.

## Step 3 — Service

- Verify job ownership.
- Verify resume version ownership when supplied.
- Enforce transition rules from the state-machine task.
- Append timeline events transactionally.

## Step 4 — API

- Replace placeholder route.
- Add pagination.
- Add 404-on-cross-user behavior.

## Step 5 — Tests

- Create/list/detail happy path.
- Cross-user job/resume 404.
- Invalid transition 422.
- Duplicate create behavior.
- Timeline/status transactional behavior.

## Validation

```bash
cd backend && pytest app/tests/test_applications_api.py
cd backend && ruff check .
```

