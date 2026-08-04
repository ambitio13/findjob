# API Integration

## Client Rules

- Use a centralized API client.
- Import generated or shared response types when available.
- Attach request IDs and auth context through the client layer.
- Normalize API errors into user-readable messages without exposing internals.

## Async Agent Runs

Long-running backend operations should return an `agent_run_id` or task ID.
The frontend should render progress and refresh by ID.

Expected statuses (canonical union, defined in
`@/features/agent-runs/status.ts` as `AgentRunStatus`):

- queued;
- running;
- succeeded;
- failed;
- not_run.

Terminal statuses (`succeeded`, `failed`, `not_run`) and active statuses
(`queued`, `running`) are exported as `TERMINAL_AGENT_RUN_STATUSES` and
`ACTIVE_AGENT_RUN_STATUSES` from the same module — import these sets instead of
re-declaring status strings at each call site.

### Polling

Use the shared `useAgentRunPolling` hook from
`@/features/agent-runs/useAgentRunPolling` for any component that polls an
agent-run detail endpoint. Do not hand-roll recursive `setTimeout` + ref/token
cancellation in a component — that pattern was duplicated across three pages and
is now centralized in the hook.

The hook accepts `runId` (null stops polling) and optional callbacks
(`onUpdate`, `onTerminal`, `isTerminal`) plus an `intervalMs` override (default
`AGENT_RUN_POLL_INTERVAL_MS`).

### Display mapping

Use `AgentRunStatusTag` from `@/features/agent-runs/AgentRunStatusTag` for
status tags, and the `AGENT_RUN_STATUS_LABEL` / `AGENT_RUN_STATUS_COLOR` maps
from the status module for any custom rendering. Keep all status → display
mapping in one place.

### Copy

Use the helper functions in `@/features/agent-runs/copy.ts`
(`asyncRunProgressMessage`, `asyncRunSuccessMessage`,
`asyncRunFailureMessage`, `asyncRetryLabel`) so async UX wording stays
consistent across workflows. Pass a `workflowLabel` (e.g. `"JD 解析"`,
`"分析"`, `"抽取"`) to scope the message.

## Type bridge

`AgentRunOut.status` is currently typed as `string` in `@/types` for
forward-compatibility with backend status additions. When checking membership
against `TERMINAL_AGENT_RUN_STATUSES` / `ACTIVE_AGENT_RUN_STATUSES` (which are
typed `ReadonlySet<AgentRunStatus>`), cast the raw status with
`as AgentRunStatus`. This is the single sanctioned escape hatch; do not widen
the sets back to `string`.

## Error UX

- Validation errors should point to the field or action that failed.
- External platform failures should name the platform and offer a retry or
  fallback when safe.
- LLM failures should not erase user input or generated drafts.

## Per-Request Timeouts

The default axios timeout (15s) is too short for endpoints that wait on the
userscript bridge or model calls. Pass an explicit `timeout` in the request
config for these cases:

| Endpoint pattern | Timeout | Reason |
| --- | --- | --- |
| Userscript bridge reads (inspect, execute) | 120s | Background-tab throttling can delay `setInterval` from 5s to ~60s |
| Model calls (match) | 60s | LLM inference can exceed 15s |
| Default (CRUD, status) | 15s | No external dependency |

## Data Freshness

Invalidate or refresh affected queries after:

- resume upload or parse;
- JD analysis;
- artifact generation;
- approval;
- submission/message execution;
- status update.

