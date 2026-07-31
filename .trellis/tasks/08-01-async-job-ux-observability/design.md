# Design

## Shared Frontend Pieces

- `useAgentRunPolling(runId, options)` or resource-specific wrappers.
- `AgentRunStatusTag`.
- `AgentRunTimeline` or reuse existing process panel after extracting it.
- `AsyncActionButton` pattern where useful.

## UX Rules

- Submit actions return control immediately.
- In-modal workflows can show progress inline and allow manual fallback.
- Page workflows can show a process panel near the result area.
- Polling stops on terminal status.
- Failure copy says what happened and what the user can do next, without raw provider stack traces.

## Data Flow

Use existing API client functions:

- `getAgentRunDetail(runId)` for step timeline.
- Resource detail endpoints for hydrated business results.
- Workflow submit endpoints for new runs.

Do not duplicate backend state in localStorage except transient raw JD draft input.
