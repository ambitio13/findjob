# API Integration

## Client Rules

- Use a centralized API client.
- Import generated or shared response types when available.
- Attach request IDs and auth context through the client layer.
- Normalize API errors into user-readable messages without exposing internals.

## Async Agent Runs

Long-running backend operations should return an `agent_run_id` or task ID.
The frontend should render progress and refresh by ID.

Expected statuses:

- queued;
- running;
- waiting for approval;
- succeeded;
- failed;
- cancelled.

## Error UX

- Validation errors should point to the field or action that failed.
- External platform failures should name the platform and offer a retry or
  fallback when safe.
- LLM failures should not erase user input or generated drafts.

## Data Freshness

Invalidate or refresh affected queries after:

- resume upload or parse;
- JD analysis;
- artifact generation;
- approval;
- submission/message execution;
- status update.

