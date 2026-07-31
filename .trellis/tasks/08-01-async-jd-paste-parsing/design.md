# Design

## Backend

Keep the public route conceptually as "parse JD", but change it into submit-and-poll:

- Validate `raw_jd` is non-empty.
- Create `AgentRun(workflow_type="jd_paste_parse", status="queued")`.
- Store only sanitized request metadata in `AgentRun.result`, such as `raw_jd_len` and `platform`.
- Enqueue `JdPasteParsePayload(user_id, agent_run_id, raw_jd, platform)`.
- Return `JdParseSubmitResponse`.

The worker handler should call a refactored service entrypoint that accepts an existing queued run instead of creating another run.

## Result Hydration

The run result should contain the typed parsed field object after success, matching the current `JdParseResponse.fields` shape. The frontend can poll `GET /agent-runs/{id}/detail` or a workflow-specific result endpoint.

## Frontend

In `JobCreateModal`:

- Parse button enqueues and immediately shows a status panel.
- Poll until terminal.
- On success, fill title/company/location/etc.
- On failure, leave raw JD in place and show retry/manual fallback.

## Compatibility

If keeping the old `JdParseResponse` name causes confusion, add new schema names and update client types in the same task.
