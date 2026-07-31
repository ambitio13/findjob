# Design

## Proposed Modules

- `app.queue.runtime`
  - queue client construction;
  - `enqueue_workflow(payload, *, job_id=None)` helper;
  - timeout/retry constants.
- `app.queue.payloads`
  - Pydantic models for `workflow_type`, `user_id`, `agent_run_id`, resource IDs, and idempotency key.
- `app.queue.worker`
  - arq `WorkerSettings`;
  - registered worker functions.
- `app.queue.handlers`
  - smoke handler for foundation tests;
  - shared failure guard that updates `AgentRun` to failed.

## Contract

API code creates durable DB state first, then calls queue runtime:

1. Validate request and ownership.
2. Create `AgentRun(status="queued")`.
3. Enqueue typed payload with `agent_run_id`.
4. Return run reference immediately.

Worker code:

1. Receives typed payload.
2. Opens `SessionLocal()`.
3. Re-loads owned resources by ID.
4. Marks run `running`.
5. Calls workflow service.
6. Marks terminal state.

## Testing Strategy

- Unit-test payload validation.
- Unit-test enqueue adapter with fake queue client.
- Test handler directly with fake model gateway where relevant.
- Keep integration tests fake-provider only; no external model network calls.

## Deployment

Add a compose service:

```yaml
worker:
  build: ./backend
  command: arq app.queue.worker.WorkerSettings
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
```

The exact command can change during implementation if the selected runtime requires it.
