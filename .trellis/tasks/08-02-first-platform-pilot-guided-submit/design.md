# Design

## Decision

Build the first platform pilot as a **guided submit** flow for **BOSS Web**
(`target_platform = "boss"`). The agent may navigate and fill a single
application form using the user's existing browser session, but it must stop
before the final submit action and wait for explicit approval.

This task is not a bulk-submit agent. It is a platform adapter plus an audited
single-application workflow that proves the safety contracts are strong enough
for later automation.

## Platform Choice

### Pilot Target

- Platform: BOSS Web.
- Scope: one application record, one job detail/application form, one selected
  resume version, one dry-run fill.
- Session model: user-provided browser session only; no username, password,
  cookies, tokens, or session secrets are stored by the backend.

### Rationale

- Existing fixtures and product copy already use `boss` as a platform value.
- The first pilot should exercise the hardest real-world risks early:
  login expiry, CAPTCHA, selector drift, duplicate action, and ambiguous final
  result.
- BOSS is acceptable only because the pilot is fill-only until approval. Any
  anti-automation or uncertain platform state is handled as a stop condition,
  not worked around silently.

## Architecture

```text
ApplicationsPage
  -> prepare platform submission
  -> POST /applications/{id}/platform-submissions/prepare
  -> create AgentRun(platform_guided_submit_prepare)
  -> worker launches platform adapter in dry-run mode
  -> adapter navigates, classifies page state, fills safe fields
  -> worker captures FilledSubmissionSnapshot
  -> creates/updates ApplicationAction(platform_submit)
  -> status: approval_required
  -> user approves exact payload_hash
  -> POST /applications/{id}/platform-submissions/{run_id}/submit
  -> assert idempotency + approval boundary
  -> adapter performs final submit or records unknown/manual result
```

The platform layer must stay isolated behind a typed adapter. Product services
should not contain selectors, DOM traversal, or browser-specific retry logic.

```text
app/services/platform_submission_service.py
app/platforms/base.py
app/platforms/boss/adapter.py
app/queue/handlers.py
```

## Browser Automation Choice

Use Playwright for the real BOSS Web pilot adapter. It has first-class support
for explicit browser contexts, deterministic selectors, screenshots/traces, and
bounded timeouts, which match the audit requirements better than a generic
agent loop.

Implementation notes:

- add the dependency in the backend package only when the adapter is
  implemented;
- keep tests on a fake adapter unless a test explicitly opts into browser
  automation;
- disable the real BOSS adapter unless an explicit environment flag is set;
- never persist Playwright storage state, cookies, headers, tokens, or traces
  containing page HTML.

## Entry Hardening

### H1 External Action Idempotency

Add a durable idempotency key before any platform call:

```text
{application_id}:{action_type}:{payload_hash}
```

Store this key on the `ApplicationAction` record. The platform submission
service must check it before launching browser automation and again before
final submit. If a terminal action with the same idempotency key already
exists, return that result instead of touching the platform.

Suggested fields:

- `external_idempotency_key`
- `external_started_at`
- `external_completed_at`
- `external_result_status`: `submitted | duplicate | unknown | failed`
- `external_result`: sanitized JSON metadata only

### H2 JD Analysis Stale-Source Detection

Before this pilot can rely on JD analysis, add stale-source detection to
`jd_analysis_service.run_resume_aware_jd_analysis_worker`:

- recompute source hash at worker execution time;
- compare it to the queued payload source hash;
- on mismatch, fail the `AgentRun` with `code = "stale_source"`;
- persist sanitized failed `AgentRun` / `AgentStep` metadata;
- do not call the model or create a `GeneratedArtifact`.

Mirror the readiness worker's stale-source behavior so both analysis and
readiness artifacts have the same freshness contract. JD analysis is job-run
scoped, not application scoped, so do not invent an `ApplicationRecord`
timeline event unless the run is explicitly tied to an application in a later
task.

## Data Contracts

### FilledSubmissionSnapshot

The dry-run output is a sanitized snapshot of exactly what would be submitted:

```json
{
  "target_platform": "boss",
  "target_resource": "job_external_id_or_url",
  "application_id": "app_id",
  "selected_artifact_ids": ["artifact_id"],
  "resume_file_reference": "resume_version_or_export_ref",
  "fields": [
    {
      "name": "message",
      "label": "开场白",
      "value": "safe visible text",
      "source_artifact_id": "artifact_id"
    }
  ],
  "attachments": [
    {
      "kind": "resume",
      "display_name": "resume.pdf",
      "reference": "resume_file_reference"
    }
  ],
  "page_state": {
    "url_hash": "sha256:...",
    "title": "safe title",
    "final_submit_selector_seen": true
  }
}
```

Do not persist raw screenshots by default. If diagnostics are needed, persist a
local diagnostic reference/path and a redacted description, not cookies or page
HTML.

### Payload Hash

The approval-boundary hash must cover the exact filled payload the user sees:

- action type;
- target platform;
- target resource;
- selected artifact IDs;
- outgoing text / filled field values;
- resume file reference;
- current source hash.

If the adapter changes any field after approval, recompute payload hash and
force reapproval.

## State Machine Mapping

| Phase | Application status | Action status | Notes |
| --- | --- | --- | --- |
| Prepare requested | `materials_ready` or `approval_required` | `draft` | Reject other statuses. |
| Browser filling | `preparing` or unchanged + active run | `draft` | No external submit allowed. |
| Filled preview ready | `approval_required` | `approval_required` | User reviews exact payload. |
| User approves | `approved` | `approved` | Approval hash is stored. |
| Final submit starts | `approved` | `approved` | Guard checks approval + idempotency first. |
| Submit confirmed | `submitted` | terminal metadata | Timeline records platform result. |
| Result ambiguous | `submitted` with `unknown` event or `failed` | `blocked` | Manual reconciliation required. |

Do not add a bulk queue that can iterate over many application records. The API
accepts one `application_id` per request.

## API Shape

```text
POST /applications/{id}/platform-submissions/prepare
GET  /applications/{id}/platform-submissions/{run_id}
POST /applications/{id}/platform-submissions/{run_id}/submit
POST /applications/{id}/platform-submissions/{run_id}/abort
```

Prepare response:

```json
{
  "run": {"id": "run_id", "status": "queued"},
  "application_id": "app_id",
  "target_platform": "boss",
  "mode": "dry_run"
}
```

Submit endpoint requirements:

- load the prepared action;
- recompute current payload hash from the filled snapshot;
- recompute current source hash;
- call `assert_action_approved(action, current_payload_hash, current_source_hash)`;
- check external idempotency key;
- call adapter final-submit only after both guards pass.

## Adapter Contract

```python
class PlatformAdapter(Protocol):
    platform: str

    async def prepare_submission(ctx: PrepareContext) -> PrepareResult:
        ...

    async def submit_prepared(ctx: SubmitContext) -> SubmitResult:
        ...
```

`PrepareResult` must be one of:

- `filled_preview`: safe snapshot ready for approval;
- `login_required`;
- `captcha_required`;
- `selector_drift`;
- `duplicate_detected`;
- `rate_limited`;
- `upload_failed`;
- `unknown`.

`SubmitResult` must be one of:

- `submitted`;
- `duplicate_detected`;
- `unknown`;
- `platform_failure`.

## Failure Handling

All platform failures persist `ApplicationFailureCategory.platform` and a
sanitized `ApplicationFailureEnvelope`.

| Failure | Detection | Envelope code | Next action | Status effect |
| --- | --- | --- | --- | --- |
| Login expired | login page or unauthenticated marker | `platform_login_required` | `manual_review` | pause/keep action blocked |
| CAPTCHA | CAPTCHA/challenge marker | `platform_captcha_required` | `manual_review` | pause; user handles challenge |
| Selector drift | required field/submit marker missing | `platform_selector_drift` | `manual_review` | failed + diagnostic ref |
| Rate limit | rate-limit copy/status page | `platform_rate_limited` | `manual_review` | pause; retry later |
| Duplicate detected | platform says already applied/contacted | `platform_duplicate_detected` | `manual_review` | timeline duplicate event |
| Upload failure | upload widget rejects file or times out | `platform_upload_failed` | `retry` if bounded, else `manual_review` | retry once, then fail |
| Unknown final result | final state cannot be classified | `platform_unknown_result` | `manual_review` | timeline requires reconciliation |

The adapter must never solve CAPTCHA, bypass rate limits, or keep clicking when
selectors drift. Those are hard stops.

## Frontend Flow

Add a guided-submit panel to the application detail page:

- `Prepare platform submission` button visible only for ready/approval states;
- active run card using shared AgentRun polling;
- filled preview table grouped by field and attachment;
- approval action panel reusing `ApplicationActionsPanel`;
- final-submit button disabled until action status is `approved`;
- manual result controls: mark submitted, mark duplicate, mark unknown, abort.

Copy must be explicit that the system is preparing a draft and will not submit
without approval.

## Observability

Each run writes AgentSteps:

1. `load_application_context`
2. `check_entry_guards`
3. `open_platform_session`
4. `navigate_to_application_form`
5. `classify_page_state`
6. `fill_form`
7. `capture_filled_snapshot`
8. `create_approval_action`
9. `submit_final` (submit endpoint only)
10. `record_result`

Timeline events:

- `platform_prepare_started`
- `platform_preview_ready`
- `platform_submit_blocked`
- `platform_submit_started`
- `platform_submit_succeeded`
- `platform_submit_unknown`
- `platform_failure`

Timeline metadata must contain IDs and hashes, not raw form HTML, credentials,
cookies, tokens, or full screenshots.

## Testing Strategy

Backend unit/integration tests:

- H1 idempotency key is computed, stored, and checked before platform calls.
- H2 stale JD analysis fails before model call and writes failure metadata.
- prepare creates an AgentRun and never calls final submit.
- submit without approval returns blocked conflict and no adapter call occurs.
- payload hash mismatch after approval blocks submit.
- duplicate idempotency key returns existing terminal result.
- all seven failure matrix rows map to sanitized envelopes.
- no bulk endpoint or batch submit path exists.
- sanitization strips cookies, tokens, credentials, HTML, raw JD, raw resume.

Frontend checks:

- lint/type-check/build.
- If a test runner is added, cover disabled final-submit button, failure panel,
  filled preview rendering, and manual unknown-result reconciliation.

## Rollout

1. Implement H1 and H2 first.
2. Build adapter interface and fake BOSS adapter for deterministic tests.
3. Implement dry-run prepare flow with no final submit path.
4. Wire approval-boundary action creation to the filled snapshot hash.
5. Add final submit endpoint behind approval + idempotency guards.
6. Enable BOSS adapter only behind an explicit environment flag.
7. Run one manual pilot with a non-critical application record.

## Non-Goals

- No autonomous bulk submission.
- No credential storage.
- No generic arbitrary-browser agent.
- No CAPTCHA solving.
- No scraping beyond the active user-selected application flow.
- No HR-message automation in this pilot.
- No recommended-jobs loop in this pilot. The future discovery/contact loop is
  documented separately in `future-recommended-jobs-loop.md` and depends on
  this single-application pilot being manually proven first.
