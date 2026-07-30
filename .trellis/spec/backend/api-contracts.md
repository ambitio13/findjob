# API Contracts

## FastAPI Boundary

FastAPI routes are transport boundaries. They should:

- declare typed request and response schemas;
- use dependency injection for auth, database sessions, and request context;
- call service-layer functions for business behavior;
- return stable error shapes;
- avoid direct LLM calls, browser automation, or complex SQL in route handlers.

## Resource Shape

Use nouns that match the product domain:

- `/users/me`
- `/resumes`
- `/resumes/{resume_id}/versions`
- `/jobs`
- `/jobs/{job_id}/analysis`
- `/applications`
- `/applications/{application_id}/artifacts`
- `/agent-runs`
- `/agent-runs/{run_id}/approve`

## Contract Rules

- Keep response objects stable for table and detail views.
- Include pagination for list endpoints from the start.
- Include status fields for asynchronous work.
- Use request IDs and operation IDs for side-effecting calls.
- Return generated artifacts by ID and metadata; avoid stuffing large generated
  documents into every list response.

## Async Workflow Shape

For long-running work, return an agent run or task ID:

```json
{
  "agent_run_id": "run_123",
  "status": "queued"
}
```

The frontend should poll, subscribe, or refresh by run ID rather than waiting
for one blocking request.

