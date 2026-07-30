# Database Guidelines

## Source of Truth

PostgreSQL owns durable business state:

- users and career preferences;
- uploaded resumes and parsed resume versions;
- discovered job postings and platform metadata;
- JD analysis results and ranking decisions;
- generated resumes, HR messages, and interview plans;
- application records and status transitions;
- agent runs, steps, tool calls, approvals, and audit events.

Redis is not durable storage. Use it for cache, rate limits, distributed locks,
temporary session context, queue coordination, and hot tool results.

## Modeling Rules

- Use stable internal IDs for all domain entities. Do not use platform display
  text as a primary key.
- Store platform IDs separately from internal IDs.
- Version mutable artifacts such as resumes, prompts, generated messages, and
  job analyses.
- Model application status as a state machine, not an unbounded text field.
- Use JSON/JSONB only for flexible external payloads, snapshots, or metadata.
  Promote frequently queried fields to normal columns.
- Add audit tables or append-only events for external actions.

## Repository Rules

- Keep query code in repository modules, not route handlers.
- Use transactions when a state transition spans multiple tables.
- Use idempotency keys for external-submission flows.
- Add indexes for user-facing table filters: status, platform, city/base,
  direction, salary range, created time, and updated time.
- Redact or avoid storing secrets from third-party platforms.

## Migration Rules

- Every schema change must include a migration.
- Migrations must be reversible or have a documented rollback plan.
- Backfills must be chunked and observable for large datasets.

