# Error Handling

## API Errors

Return structured errors from API boundaries. Include a stable error code,
human-readable message, and request ID. Do not leak stack traces, prompts,
credentials, or raw platform payloads to clients.

Use clear categories:

- validation error;
- authentication or authorization error;
- not found;
- conflict or duplicate action;
- external platform failure;
- LLM/provider failure;
- rate limit or quota;
- internal server error.

## Agent and Tool Errors

Agent execution should preserve a failure trail:

- planner step that produced the action;
- tool name and input schema version;
- validation result;
- external response category;
- retry count;
- final state.

If a tool repeatedly fails, stop the loop, mark the step failed, and either
re-plan with a bounded retry count or ask the user for input. Do not let an
agent call the same failing tool indefinitely.

## User Cancellation

Long-running job-search and platform-operation tasks must support cancellation.
On cancellation, persist the last completed step and mark unfinished side effects
as not executed.

