# Backend Quality Checklist

Run the relevant checks once backend tooling exists. Until then, use this file
as the review checklist for backend design work.

## Required Checks

- FastAPI routes validate input and delegate business logic to services.
- Pydantic schemas exist for API inputs and outputs.
- Database writes are owned by repositories or services, not UI-facing handlers.
- External side effects require permission checks and audit logs.
- Agent loops have bounded retries, timeouts, and cancellation behavior.
- Sensitive data is redacted from logs.
- Tests cover state transitions and side-effect idempotency.

## Expected Commands

After package manifests exist, keep this section updated with the actual project
commands. Good targets are:

```bash
pytest
ruff check
mypy
docker compose up --build
```

