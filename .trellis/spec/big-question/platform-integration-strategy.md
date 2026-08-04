# Job Platform Integration Strategy

## Current Status

The product should cover many resume-delivery platforms, but the initiation
materials leave open whether integrations are API-based, browser-automation
based, or agent-driven.

## Preferred Order

1. Official API or documented partner integration.
2. User-authorized import/export flow.
3. Browser automation behind platform-specific adapters.
   - Prefer in-page userscript (Tampermonkey) or browser extension over
     CDP/Playwright when the platform performs protocol-level automation
     detection (see BOSS findings below).
4. Manual copy-ready drafts when automation is unsafe or unstable.

## BOSS 直聘 Anti-Automation Findings

BOSS直聘 performs CDP protocol-level detection. Experiments documented in
`docs/boss-anti-automation-findings.md` show:

- `Page.navigate` followed by `Runtime.evaluate` triggers detection and page
  destruction.
- Detection is cumulative — repeated CDP sessions on the same profile escalate
  restrictions.
- In-page JavaScript execution via CDP (`Runtime.evaluate`) is itself a signal.

## Userscript Bridge Approach

When a platform performs protocol-level automation detection, use a Tampermonkey
userscript bridge instead of CDP/Playwright:

- The userscript runs in the page's own JS context — no CDP signature, no
  `Runtime.evaluate`, no `Page.navigate`.
- The backend sends sanitized instructions via HTTP; the userscript only executes
  them and posts back sanitized results.
- Instructions are constructed entirely by the backend (selectors, fill values).
  The userscript never hardcodes selectors or credentials.
- The instruction queue is pure in-memory (`asyncio.Queue`); it is never
  persisted to Redis or the database.
- Results carry sanitized values by default: `visible`, `count`, `title`
  (truncated), `url_hash` (sha256), `error` (diagnostic-stripped).
- Raw HTML and raw resume never cross the channel.
- Raw JD is also forbidden by default. The only exception is an explicit,
  scoped `read_jd` instruction for a user-initiated BOSS job-read workflow. It
  may return bounded, cleaned JD text and structured job fields, but never raw
  HTML, cookies, tokens, localStorage, scripts, or complete page dumps.
- The userscript session endpoint and any session token are process
  configuration only — never in request payload, queue, or DB.

## Design Decision: Userscript Executor, Not Userscript Agent

**Context**: A Tampermonkey script can technically read JD text, match keywords,
click "立即沟通", and send a message by itself. That is not the chosen
architecture.

**Options Considered**:

1. Pure userscript automation — faster to prototype, but policy, prompts,
   approval, idempotency, retries, and audit live inside a third-party page
   script.
2. Backend agent with userscript executor — slower to implement, but preserves
   the product's service-layer state, audit trail, multi-platform adapter
   boundary, prompt/version provenance, and failure handling.

**Decision**: Use the backend agent with a minimal userscript executor. The
userscript may read scoped page data and execute bounded page actions only after
the backend sends an instruction. It must not decide whether a job matches, what
message to send, whether an action is approved, or whether to continue to the
next job.

**Why**: External communication is a user-visible side effect. It must be
covered by backend idempotency keys, approval payload hashes, timeline events,
failure envelopes, and manual reconciliation paths.

**Wrong**:

```text
Tampermonkey reads every recommended job -> keyword matches in JS -> clicks
立即沟通 -> sends a template -> loops to the next job.
```

**Correct**:

```text
Backend requests read_jd for the active page -> backend parses/matches/generates
opening message -> backend creates approved/idempotent action -> userscript
executes one bounded click/fill/send sequence -> backend records the terminal
result.
```

## Scenario: BOSS Userscript JD Read And Immediate Communication

### 1. Scope / Trigger

- Trigger: the agent needs to touch the user's real BOSS browser page, read the
  current JD, and optionally send an opening message.
- This is cross-layer work: userscript bridge, backend API/service, platform
  adapter, application action state, frontend approval UI, and runbook.

### 2. Signatures

Bridge operations:

- `read_jd`
- `click_immediate_communicate`
- `fill_opening_message`
- `send_opening_message`
- `read_communication_result`

Backend platform adapter capabilities should remain platform-scoped:

```python
async def read_current_jd(ctx: BrowserPageContext) -> ReadJobResult
async def prepare_communication(ctx: CommunicationPrepareContext) -> CommunicationPrepareResult
async def execute_communication(ctx: CommunicationExecuteContext) -> CommunicationExecuteResult
```

The service layer should call these through BOSS-specific orchestration first;
do not expose a generic arbitrary-browser-control endpoint.

### 3. Contracts

- Every bridge instruction includes `instruction_id`, `op`, `page_id`, and
  `expected_url_hash`.
- `read_jd` may return structured job fields and bounded cleaned JD text:
  `title`, `company`, `location`, `salary`, `experience`, `education`,
  `skills`, `description`, `source_kind`, `page_url_hash`.
- Communication actions include `application_id`, `platform`, `job_url_hash`,
  `resume_version_id`, `payload_preview`, `payload_hash`, `idempotency_key`,
  `decision_trace`, and `external_result`.
- The bridge queue remains in-memory only. Persist only validated product data
  and action/timeline records, not queued instructions.

### 4. Validation & Error Matrix

- Bridge disconnected -> `bridge_not_connected`, retry after page/backend fix.
- Page/session mismatch -> `page_binding_mismatch`, hard stop.
- Current page is not a BOSS job/recommended card -> `page_not_supported`, hard
  stop.
- JD is missing required fields -> `jd_too_sparse`, review or skip.
- Match output is invalid/low confidence -> `match_needs_review` or skip, no
  browser side effect.
- Approval is missing/stale -> `approval_required` or `approval_stale`, no
  browser side effect.
- Duplicate idempotency key -> return existing terminal result, no new click.
- CAPTCHA/rate limit/unknown send state -> hard stop and manual reconciliation.
- Selector drift (entry buttons invisible) -> `selector_drift` failure code,
  hard stop, no click/fill sent. See "Selector Drift Detection" below.

### 5. Good/Base/Bad Cases

- Good: active BOSS job page matches the bound `page_id` and URL hash; `read_jd`
  returns enough fields; backend decides `communicate`; approved action sends
  once and records `succeeded`.
- Base: JD reads successfully but match decision is `skip`; system records the
  reason and sends no browser action.
- Bad: two BOSS tabs are open; a non-active tab polls the bridge. The backend
  must not let it consume or complete the instruction.

### 6. Tests Required

- Unit tests for page/session binding and URL hash mismatch.
- Bridge API tests for `read_jd` payload validation and result rejection.
- Adapter tests proving prepare/read never clicks.
- Service tests proving unapproved/stale/duplicate actions cannot execute.
- End-to-end/manual runbook checks for one real BOSS dry-run before real send.

### 7. Wrong vs Correct

#### Wrong

```javascript
// Userscript owns product policy and loops forever.
if (document.body.innerText.includes("Python")) {
  document.querySelector("button").click();
  sendMessage("您好，我很感兴趣");
}
```

#### Correct

```text
Userscript sends heartbeat -> backend binds page -> backend sends read_jd ->
backend decides and records action -> backend sends one approved instruction
sequence -> userscript returns sanitized terminal result.
```

## Adapter Rules

- Each platform gets its own adapter with typed capabilities.
- Adapter methods must describe whether they are read-only or state-changing.
- State-changing methods require approval records and idempotency keys.
- Rate limits and anti-duplication behavior are part of the adapter contract.
- Platform-specific failures should map to shared backend error categories.
- A platform may have multiple adapter implementations (CDP, userscript, fake)
  behind the same `PlatformAdapter` protocol. Selection is config-driven and
  never exposed to the service layer.

## Do Not Do

- Do not let a generic agent control arbitrary browser state without a sandbox.
- Do not mix platform selectors and scraping logic into product services.
- Do not submit resumes or messages during discovery or analysis steps.
- Do not persist instruction queues or raw page content to Redis or PostgreSQL.
- Do not send raw credentials, cookies, or profile paths through the bridge
  channel.
- Do not implement BOSS matching, prompt generation, approval, idempotency, or
  retry policy inside the Tampermonkey script.
- Do not let a userscript automatically loop through recommended jobs. The
  larger recommendation loop must be backend-orchestrated and gated by the
  one-job pilot metrics.

## Selector Drift Detection

BOSS page markup changes over time. When the entry-point buttons
(`IMMEDIATE_COMMUNICATE_BUTTON`, `CONTINUE_COMMUNICATE_BUTTON`) are no longer
visible on a bound job-detail page, the backend must not guess which element to
click.

### Where

Drift detection lives in the platform adapter's `execute_communication()`
method, not in the service-layer `prepare_communicate_action()`. The prepare
step is a pure DB operation (it builds the action record, payload hash, and
idempotency key) and never touches the browser. Only `execute_communication()`
talks to the browser via the userscript bridge.

### How

Before clicking 立即沟通, the adapter sends two read-only `check_visible`
instructions (one per entry button). If **both** are invisible:

- Return `CommunicationOutcome.failed` with `failure_code="selector_drift"`.
- Set `diagnostic_reference` to a sanitized selector name (e.g.
  `selector_drift_immediate_communicate`), not a raw CSS string.
- Do **not** send any `click_immediate_communicate`, `fill_opening_message`, or
  `send_opening_message` instruction. The flow short-circuits before any
  side-effecting action.

### Safety

The drift check is read-only (`check_visible` returns `visible: bool` + `count:
int`, no DOM mutation). It does not violate the semi-auto loop safety
invariant: execute still requires human confirmation, and the check itself adds
no new side effects.

### If Only One Button Is Visible

If at least one entry button is visible, the drift check passes and the flow
proceeds normally. The subsequent `click_immediate_communicate` instruction will
fail if the wrong button was chosen — that is an ordinary platform failure, not
a drift event.

### Test Conventions

- `FakeUserscriptChannel` supports 3-tuple lookup keys
  `(op, selector_value, selector_name)` to disambiguate role selectors that
  share the same `value` (e.g. both entry buttons are `value="button"`).
  2-tuple `(op, selector_value)` remains a fallback.
- Every communicate test whose flow passes Step 3.5 must seed a
  `check_visible` → `visible=True` entry for `IMMEDIATE_COMMUNICATE_BUTTON` in
  its `result_map`, otherwise the drift check will short-circuit the flow.

