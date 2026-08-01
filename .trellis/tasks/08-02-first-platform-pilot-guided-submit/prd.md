# First Platform Pilot — Guided Submit

## Status

**Planning draft.** Created by the automation-readiness review
(`08-01-automation-readiness-review`) as the "go" outcome. Not yet activated.
Two entry conditions (H1, H2) must close before this task starts.

## Goal

Run the first semi-automatic platform application pilot: agent navigates a
single job platform, fills the application form, and **stops before final
submit** for explicit user approval. This is guided submit, not autonomous
bulk submission.

## Entry Conditions (must close before task.py start)

### H1 — External Action Idempotency Key

Before any platform call, define and implement an idempotency key for external
actions:

- Key shape: `{application_id}:{action_type}:{payload_hash}`
- Prevents duplicate platform submit when the user retries or the agent
  re-runs after a network blip.
- Store on the approval/action record; check before any platform HTTP/browser
  call.

### H2 — JD Analysis Stale-Source Detection

`jd_analysis_service.run_resume_aware_jd_analysis_worker` currently has no
stale-source check (unlike the readiness worker). Add source-hash recompute +
`stale_source` failure mirroring `readiness_service.py:907-979` before the
pilot relies on jd_analysis freshness.

## Scope

### In Scope

- One target platform (selection is a pilot decision — pick the one with the
  most stable form structure).
- Dry-run mode: agent navigates + fills, captures page state, stops before
  final submit.
- Reuse the existing approval boundary: bind to
  `compute_payload_hash` of the exact filled form; `assert_action_approved`
  before submit.
- Login/session handling via user-provided session (no credential storage).
- Platform failure matrix: login expiry, CAPTCHA, selector drift, rate limit,
  duplicate detected, upload failure, unknown final result.
- `submitted` / `unknown` result handling on ApplicationRecord timeline.

### Out of Scope

- Autonomous bulk submission across multiple applications.
- Auto-login / credential storage.
- HR messaging automation (second pilot iteration).
- Multi-platform support (one platform only).
- Resume Word/PDF export.

## Required Behavior

### Guided Submit Flow

```text
user selects application record (status: materials_ready / approval_required)
  -> user triggers "prepare platform submission"
  -> agent opens platform, navigates to job application form (dry-run)
  -> agent fills form fields from readiness artifacts
  -> agent captures page state + filled payload snapshot
  -> agent stops, status -> approval_required
  -> user reviews exact page/payload (bound to approval payload_hash)
  -> user approves via approval boundary
  -> agent executes final submit (or user manually submits)
  -> timeline records submitted / unknown
```

### Platform Failure Matrix

| Failure | Required behavior |
| --- | --- |
| Login expired | stop, `platform` failure envelope, ask user to log in |
| CAPTCHA | pause, user handles challenge, resume or abort |
| Selector drift | fail with diagnostic screenshot/log reference in `latest_error` |
| Rate limit | stop, `next_action=manual_review`, retry later |
| Duplicate detected | stop, mark duplicate, tie to H1 idempotency key |
| Upload failure | bounded retry if safe, otherwise `platform` failure |
| Unknown final result | mark `submitted/unknown`, `next_action=manual_review`, ask user to reconcile |

All failures use `ApplicationFailureCategory.platform` and persist a sanitized
`ApplicationFailureEnvelope` + timeline event, reusing the existing envelope
infrastructure.

## Acceptance Criteria

- [ ] H1 (external idempotency key) is implemented and tested.
- [ ] H2 (jd_analysis stale detection) is implemented and tested.
- [ ] One platform is selected and documented in design.md.
- [ ] Dry-run navigation + fill works end-to-end and stops before submit.
- [ ] Approval boundary binds the exact filled-form payload_hash; submit is
      impossible without approval.
- [ ] All 7 platform failure categories have detection + handling tests.
- [ ] `submitted` and `unknown` results are recorded on the timeline.
- [ ] No credential or session-secret is persisted (sanitization verified).
- [ ] A regression test asserts no autonomous bulk-submit path exists.
- [ ] Backend tests, ruff, frontend lint/type-check all pass.

## Notes

- This task is explicitly **semi-automatic**. The first iteration must not
  perform autonomous bulk submission.
- Reuse existing infrastructure: `AgentRun`/`AgentStep` for the platform
  navigation run, `ApplicationFailureEnvelope` for failures, approval boundary
  for the checkpoint, timeline for audit.
- Browser automation library choice (Playwright/Selenium/etc.) is a design.md
  decision, not a PRD decision.
