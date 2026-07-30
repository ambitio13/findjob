# Agent Safety and Sandbox

## Why This Matters

The initiation notes explicitly warn that prompt-only constraints are
insufficient. The system needs global sandbox controls around model output and
tool execution.

## Required Safety Layers

- Tool registry with explicit capabilities.
- Input schema validation before execution.
- Permission and ownership checks.
- Human approval gates for external side effects.
- Idempotency keys for actions that may be retried.
- Rate limits per user and platform.
- Audit logs for every state-changing tool call.
- Output filtering before generated content is shown or sent.

## Agent Loop Controls

- Set maximum planning/execution/reflection rounds.
- Stop repeated calls to the same failing tool.
- Time out slow tool calls.
- Provide cancellation and human handoff.
- Persist enough step state to explain what happened.

