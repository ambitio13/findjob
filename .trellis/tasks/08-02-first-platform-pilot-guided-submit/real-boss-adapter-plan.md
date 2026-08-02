# Real BOSS Adapter Completion Plan

## Current State

The safety-contract slice is implemented and verified:

- H1 external idempotency fields + guards exist.
- H2 JD analysis stale-source detection exists.
- Platform adapter protocol and fake BOSS adapter exist.
- Prepare/submit/abort APIs exist.
- Frontend guided-submit panel exists.
- Real BOSS adapter is env-gated and currently returns safe `unknown` outcomes.

The remaining blocker is the PRD acceptance item:

> Dry-run navigation + fill works end-to-end and stops before submit.

This plan is the execution path to close that blocker without weakening the
approval/idempotency boundary.

## Guiding Constraints

- One `application_id` per run; no batch endpoint, no loop over applications.
- Prepare phase may navigate and fill, but must never click final submit.
- Final submit may run only after:
  - external idempotency guard passes;
  - `assert_action_approved` passes with current payload/source hashes.
- Never persist credentials, cookies, tokens, Playwright storage state, raw page
  HTML, raw JD, or raw resume.
- CAPTCHA, rate limit, selector drift, and ambiguous page state are hard stops.
- Do not write generic browser-agent behavior into product services; all
  selectors and browser logic stay inside `app/platforms/boss/`.

## Phase 0 — Session Handoff Contract

Problem: current `PrepareContext.session_reference` exists, but the API/service
passes `None`. A real BOSS session needs a safe local handoff mechanism before
navigation can work.

Recommended pilot approach:

- Add environment-only local session config:
  - `BOSS_ADAPTER_ENABLED=1`
  - `BOSS_SESSION_PROFILE_DIR=/absolute/local/path`
- The backend opens a Playwright persistent context from that local profile
  directory.
- The path is process config, not request payload, queue payload, or database
  state.
- If the profile is missing or not logged in, classify as `login_required`.

Implementation notes:

- Add settings fields in `backend/app/core/config.py`.
- Keep `session_reference` out of persisted `AgentRun.result`, `AgentStep`,
  `ApplicationAction`, and timeline metadata.
- Add tests that secret-looking profile paths/session strings are not persisted.

Acceptance:

- With `BOSS_ADAPTER_ENABLED` unset, fake adapter remains default.
- With `BOSS_ADAPTER_ENABLED=1` and missing profile config, adapter returns
  `login_required` or `unknown`, never `filled_preview`.
- No session/profile value appears in DB result blobs or API responses.

## Phase 1 — Playwright Runtime Wrapper

Create a small BOSS browser runtime wrapper inside
`backend/app/platforms/boss/`:

```text
boss/
  adapter.py
  runtime.py
  selectors.py
  classifiers.py
  sanitizer.py
```

Responsibilities:

- launch persistent context with bounded timeout;
- open exactly one page for `target_resource`;
- close context/page deterministically;
- collect only sanitized diagnostics:
  - url hash;
  - safe page title;
  - classification code;
  - optional diagnostic reference, not raw HTML.

Do not add screenshots/traces as default persisted artifacts. If a local
diagnostic image is useful during manual testing, store only a local reference
and make redaction/manual cleanup explicit.

Acceptance:

- Unit tests cover timeout/context close behavior with a fake Playwright object.
- No raw page HTML, cookies, headers, tokens, or storage state leave the runtime.

## Phase 2 — Page Classification Before Fill

Before any field is filled, classify the current page:

| Classifier | Adapter result |
| --- | --- |
| Login page / unauthenticated marker | `login_required` |
| CAPTCHA/challenge marker | `captcha_required` |
| Rate-limit marker | `rate_limited` |
| Already applied / duplicate marker | `duplicate_detected` |
| Missing required form anchors | `selector_drift` |
| Form anchors visible | continue to fill |
| Anything ambiguous | `unknown` |

The classifier must be conservative: if it cannot confidently identify a safe
fillable form, it returns a hard-stop outcome.

Acceptance:

- Tests cover all seven PRD failure categories using saved minimal HTML
  fixtures or fake locator responses.
- `selector_drift` includes a diagnostic reference/code but no HTML.
- CAPTCHA and rate-limit paths do not retry or attempt workarounds.

## Phase 3 — Fill-Only Prepare

Implement `RealBossAdapter.prepare_submission`:

1. Open target resource from the configured local session profile.
2. Classify page state.
3. Fill only safe fields mapped from the approved readiness inputs:
   - outgoing message text;
   - resume upload/reference only if the platform form exposes a safe upload
     widget and the file reference is available.
4. Verify final submit control is visible.
5. Stop before submit.
6. Return `FilledSubmissionSnapshot`.

Selector strategy:

- Use a centralized selector registry in `selectors.py`.
- Prefer stable semantic locators (`get_by_role`, label text, placeholder)
  over brittle CSS chains.
- Store selector names in code, not in DB.
- If any required locator is missing, return `selector_drift`.

Acceptance:

- Prepare returns `filled_preview` only when all required fields are filled and
  the final submit control is visible.
- Prepare never calls `click()` on the final submit control.
- Snapshot contains only the approvable fields and page-state metadata.
- A regression test asserts final-submit selector is observed but not clicked.

## Phase 4 — Final Submit Implementation

Implement `RealBossAdapter.submit_prepared` only after Phase 3 passes.

Flow:

1. Reopen the same target resource/session.
2. Re-classify page state using the same conservative classifiers.
3. Re-apply/verify the filled values from `SubmitContext.filled_snapshot`.
4. Click final submit once.
5. Classify result:
   - clear success confirmation -> `submitted`;
   - duplicate marker -> `duplicate_detected`;
   - ambiguous navigation/toast/no confirmation -> `unknown`;
   - upload or platform error -> `platform_failure`.

Acceptance:

- The adapter performs at most one final-submit click per call.
- `submitted` is returned only from an observed success marker, not from lack of
  error.
- Unknown final result is persisted as `unknown` and never silently retried.
- Idempotency replay returns existing terminal result without adapter call
  (already covered by service tests; keep it green).

## Phase 5 — Manual Pilot Script

Add a local-only pilot runbook:

```text
docs/manual-boss-pilot.md or task runbook section
```

Runbook must include:

- how to create/use the local BOSS session profile;
- environment flags;
- how to pick a non-critical application record;
- exact stop-before-submit verification;
- what to capture after login/CAPTCHA/selector/rate-limit failures;
- rollback/manual reconciliation steps.

Acceptance:

- A developer can run prepare without storing credentials.
- The runbook explicitly says not to enable real submit against a critical job
  until one dry-run has been manually inspected.

## Phase 6 — Validation Matrix

Required commands:

```bash
cd backend && .venv/bin/ruff check .
cd backend && APP_ENV=test MODEL_PROVIDER=fake DATABASE_URL='postgresql+psycopg://app:app@localhost:5432/job_search_agent_test' .venv/bin/pytest -q app/tests/test_platform_boss_adapter.py app/tests/test_platform_submission_api.py
cd backend && APP_ENV=test MODEL_PROVIDER=fake DATABASE_URL='postgresql+psycopg://app:app@localhost:5432/job_search_agent_test' .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 ./.trellis/scripts/task.py validate 08-02-first-platform-pilot-guided-submit
git diff --check
```

Manual validation:

- fake adapter remains default;
- real adapter refuses to run without explicit env flag + profile config;
- real prepare stops before submit;
- login/CAPTCHA/rate-limit/selector-drift/duplicate/upload/unknown outcomes are
  visible in `latest_error` and timeline;
- final submit is impossible before approval.

## Definition Of Done

This task can be marked complete only when:

- `RealBossAdapter.prepare_submission` performs real Playwright navigation,
  classification, fill, and stop-before-submit behavior;
- `RealBossAdapter.submit_prepared` returns `submitted` only after an observed
  success marker;
- the seven platform failure categories are covered by deterministic tests;
- no credentials/session data/page HTML are persisted;
- the manual pilot runbook exists;
- all automated quality gates pass;
- `check.jsonl` records the manual dry-run evidence or explicitly states why it
  remains blocked.
