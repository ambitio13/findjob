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

## Browser Side-Effect Controls

When an agent touches a real third-party browser page through a platform
adapter, browser actions are external side effects. Treat them like writes to an
external system, even if the local mechanism is a userscript.

Required controls:

- Bind every browser instruction to a concrete page/session identifier and an
  expected sanitized URL hash.
- Re-check page binding immediately before any click, fill, upload, or send.
- Create an action draft before any external side effect.
- Require an approval payload hash unless the task is explicitly configured as
  a dry-run.
- Store an idempotency key before execution starts.
- Emit timeline and AgentRun events for draft, approval, running, terminal
  result, and failure.
- Never automatically retry an action after an unknown external result.

## Scenario: BOSS JD Read And Communicate Safety Contract

### 1. Scope / Trigger

- Trigger: the agent reads JD text from a real BOSS page and may send an opening
  message through "立即沟通".
- This combines a read from a third-party page with an external communication
  side effect.

### 2. Signatures

Action type:

```text
boss_immediate_communicate
```

Required browser instructions:

```text
read_jd
click_immediate_communicate
fill_opening_message
send_opening_message
read_communication_result
```

### 3. Contracts

- `read_jd` may return bounded cleaned JD text and structured fields only.
- `click_immediate_communicate`, `fill_opening_message`, and
  `send_opening_message` require page/session binding.
- Sending requires an approved action with payload hash and idempotency key.
- Terminal result must be one of `succeeded`, `duplicate`, `failed`, or
  `unknown`.

### 4. Validation & Error Matrix

- Missing bridge heartbeat -> login/browser recovery, no action.
- Page binding mismatch -> hard stop, no action.
- CAPTCHA/rate limit -> hard stop, user handles platform state.
- Match decision `skip`/`needs_review` -> no browser side effect.
- Missing/stale approval -> no browser side effect.
- Duplicate idempotency key -> no new browser side effect.
- Unknown send result -> manual reconciliation, no automatic second click.

### 5. Good/Base/Bad Cases

- Good: approved action sends once, result marker confirms success, timeline
  records `succeeded`.
- Base: JD is readable but below threshold; timeline records skip reason.
- Bad: send button click times out; agent clicks again automatically. This is
  forbidden because the first click may have succeeded externally.

### 6. Tests Required

- Action execution fails closed when approval is absent or stale.
- Duplicate idempotency key returns prior result without calling the adapter.
- Page binding mismatch prevents click/fill/send.
- Unknown communication result records `unknown` and does not retry.

### 7. Wrong vs Correct

#### Wrong

```text
If send times out, retry click until a success marker appears.
```

#### Correct

```text
If send result is unknown, stop, record unknown, and require manual
reconciliation before any future action for that job.
```
