# Implementation Plan

## Preconditions

This task may start only after the agent has read:

- `prd.md`
- `design.md`
- `.trellis/tasks/archive/2026-08/08-01-automation-readiness-review/review.md`
- related backend/frontend/shared specs listed in `implement.jsonl`

Do not implement autonomous bulk submission. Every API and worker must operate
on exactly one `application_id`.

## Step 1 — H1 External Action Idempotency

- Extend the application action domain with an external idempotency key:
  `{application_id}:{action_type}:{payload_hash}`.
- Persist the key before any browser/platform call.
- Add a service guard that:
  - returns an existing terminal result for the same key;
  - blocks a duplicate running action;
  - records `unknown` instead of retrying blindly when final platform state is
    ambiguous.
- Add tests proving the platform adapter is not called when the idempotency
  guard blocks execution.

Expected files:

- `backend/app/db/models/models.py`
- `backend/alembic/versions/*`
- `backend/app/schemas/application_action.py`
- `backend/app/services/approval_action_service.py`
- `backend/app/services/approval_boundary.py`
- `backend/app/tests/test_application_actions.py`

## Step 2 — H2 JD Analysis Stale-Source Detection

- Add source-hash payload data to the JD analysis async payload if missing.
- In `jd_analysis_service.run_resume_aware_jd_analysis_worker`, recompute the
  current source hash before model execution.
- If the hash differs, fail the run with `code = "stale_source"` and persist
  sanitized `AgentRun` / `AgentStep` metadata.
- Mirror the readiness worker's behavior rather than inventing a second stale
  contract.
- Do not invent an `ApplicationRecord` timeline event for JD analysis unless a
  later task explicitly ties the JD analysis run to an application.
- Add regression tests proving stale JD analysis does not call the model and
  does not persist a new artifact.

Expected files:

- `backend/app/queue/payloads.py`
- `backend/app/services/jd_analysis_service.py`
- `backend/app/tests/test_jd_parse_api.py`
- `backend/app/tests/test_jd_parse_contracts.py`

## Step 3 — Platform Adapter Interface

- Create a typed platform adapter boundary:
  - base protocol/types in `backend/app/platforms/base.py`;
  - BOSS adapter in `backend/app/platforms/boss/adapter.py`;
  - fake adapter for deterministic tests.
- Use Playwright for the real BOSS Web adapter, but keep default tests on the
  fake adapter and enable the real adapter only behind an explicit environment
  flag.
- Adapter result types must classify:
  - `filled_preview`;
  - `login_required`;
  - `captcha_required`;
  - `selector_drift`;
  - `rate_limited`;
  - `duplicate_detected`;
  - `upload_failed`;
  - `unknown`;
  - `submitted`.
- Product services may call the adapter but must not contain selectors or DOM
  traversal.

Expected files:

- `backend/app/platforms/__init__.py`
- `backend/app/platforms/base.py`
- `backend/app/platforms/boss/__init__.py`
- `backend/app/platforms/boss/adapter.py`
- `backend/app/tests/test_platform_boss_adapter.py`

## Step 4 — Prepare Guided Submission

- Add a `platform_guided_submit_prepare` AgentRun workflow.
- Add `POST /applications/{id}/platform-submissions/prepare`.
- Validate application status is `materials_ready` or `approval_required`.
- Load readiness artifacts and current source snapshot.
- Run BOSS adapter in dry-run/fill-only mode.
- Persist a sanitized `FilledSubmissionSnapshot`.
- Create/update a `platform_submit` `ApplicationAction` whose payload hash is
  computed from the exact filled snapshot.
- Set application status to `approval_required`.

Expected files:

- `backend/app/api/v1/applications.py` or a focused router module
- `backend/app/queue/handlers.py`
- `backend/app/queue/runtime.py`
- `backend/app/services/platform_submission_service.py`
- `backend/app/tests/test_platform_submission_api.py`

## Step 5 — Final Submit Behind Guards

- Add `POST /applications/{id}/platform-submissions/{run_id}/submit`.
- Before adapter final submit:
  - recompute current payload hash;
  - recompute current source hash;
  - call `assert_action_approved`;
  - check external idempotency key.
- On confirmed submit, append timeline event and set status to `submitted`.
- On duplicate, rate limit, CAPTCHA, selector drift, upload failure, or unknown
  final result, persist the platform failure/result exactly as described in
  `design.md`.
- Do not add a batch endpoint.

Expected files:

- `backend/app/services/platform_submission_service.py`
- `backend/app/services/application_service.py`
- `backend/app/tests/test_platform_submission_api.py`
- `backend/app/tests/test_application_state.py`

## Step 6 — Frontend Guided Submit Panel

- Add a guided-submit panel to application detail:
  - prepare button;
  - active run status using shared AgentRun polling;
  - filled preview table;
  - approval action slot;
  - final submit button disabled until approved;
  - manual result controls: submitted, duplicate, unknown, abort.
- Copy must say the system will not submit without approval.
- Do not show raw credentials/session data or raw page HTML.

Expected files:

- `frontend/src/api/client.ts`
- `frontend/src/types/index.ts`
- `frontend/src/pages/applications/ApplicationsPage.tsx`
- `frontend/src/features/applications/*`

## Step 7 — Failure Matrix Tests

Add focused tests for all seven platform failure categories:

- login expired;
- CAPTCHA;
- selector drift;
- rate limit;
- duplicate detected;
- upload failure;
- unknown final result.

Each test must assert:

- adapter classification;
- sanitized `ApplicationFailureEnvelope`;
- timeline event;
- correct next action;
- no raw cookies/tokens/credentials/raw JD/raw resume/page HTML persisted.

## Validation

Run checks serially when they share the same PostgreSQL test database.

```bash
cd backend && .venv/bin/ruff check .
cd backend && APP_ENV=test MODEL_PROVIDER=fake DATABASE_URL='postgresql+psycopg://app:app@localhost:5432/job_search_agent_test' .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 ./.trellis/scripts/task.py validate 08-02-first-platform-pilot-guided-submit
git diff --check
```

## Completion Criteria

- H1 and H2 are implemented and tested.
- BOSS Web is the only enabled pilot platform.
- Prepare flow fills and stops before final submit.
- Final submit is impossible without approval and idempotency checks.
- All platform failures are surfaced through the shared failure envelope.
- No bulk/autonomous submit path exists.
- Task `check.jsonl` records commands, results, and any residual risk.

## Remaining Blocker Plan — Real BOSS Adapter

The safety-contract slice can pass automated tests with the fake adapter, but
the task is not complete until the env-gated `RealBossAdapter` performs real
Playwright navigation/fill/classification. Follow
`real-boss-adapter-plan.md` before marking this task complete.

Execution order:

1. Session handoff contract:
   - add environment-only local profile config;
   - keep session references out of DB, queue payloads, logs, and API
     responses;
   - missing/unusable session returns `login_required` or `unknown`.
2. Playwright runtime wrapper:
   - bounded launch/navigation/fill timeouts;
   - deterministic context/page cleanup;
   - sanitized diagnostics only.
3. Page classification:
   - login expired;
   - CAPTCHA;
   - selector drift;
   - rate limit;
   - duplicate detected;
   - upload failure;
   - unknown state.
4. Fill-only prepare:
   - fill safe fields;
   - observe final-submit control;
   - never click final submit;
   - return `FilledSubmissionSnapshot`.
5. Final submit:
   - run only after service approval/idempotency guards;
   - click final submit at most once;
   - return `submitted` only from an observed success marker;
   - otherwise return duplicate/unknown/platform_failure.
6. Manual pilot runbook:
   - local BOSS profile setup;
   - env flags;
   - non-critical application record;
   - dry-run evidence and rollback steps.

Do not replace the current safe `unknown` stub with a synthetic success. Until
real page evidence exists, unknown is the correct conservative outcome.
