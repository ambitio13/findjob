# Cross-Layer Thinking Guide

Use this guide when a change touches more than one layer.

## Trace the Flow

For every feature, write the path in this order:

1. User intent.
2. Frontend form, table, or detail view.
3. API request and validation.
4. Backend domain service.
5. Agent planner or tool adapter, if any.
6. PostgreSQL write or read.
7. Redis cache, queue, rate limit, or lock.
8. Logs, metrics, and audit trail.
9. User-visible result.

## Job Application Example

When the agent prepares an HR opening message:

- The frontend collects the target job and resume version.
- The backend validates that both belong to the current user.
- The agent uses the JD, user profile, and resume facts to generate a short
  message.
- The system stores the prompt version, model/provider, output, and source IDs.
- The user reviews and approves before any platform adapter sends it.
- The platform adapter records idempotency keys and delivery result.
- The application record moves to the next status only after the adapter result
  is known.

## Common Failure Modes

- Frontend displays an AI recommendation as if it were confirmed fact.
- Backend stores LLM output without source IDs or prompt version.
- Agent tool call bypasses a user approval gate.
- Retry logic submits the same resume twice.
- Cache returns stale job analysis after the JD or resume version changed.
- Logs include resume content, credentials, or private HR conversation text.

