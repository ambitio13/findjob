# Automation Readiness Review

## Decision

**GO** — with two pre-pilot hardening items that must close before the first
platform automation task starts, and one lower-priority improvement tracked as
a residual risk.

The internal readiness envelope (state machine, failure handling, retry/
idempotency, provenance, audit timeline, approval boundary) is sound, tested,
and deliberately terminates before any external execution. The project may
proceed to plan a first **semi-automatic, dry-run + fill-only** platform pilot.
Autonomous bulk submission is explicitly rejected for this iteration.

The two mandatory hardening items are not readiness-envelope defects — they are
gaps that a platform pilot would immediately hit, so they gate the pilot task
rather than this review.

---

## Evidence — Build & Test Status

| Check | Command | Result |
| --- | --- | --- |
| Backend tests | `cd backend && ./.venv/bin/python -m pytest -q` | **406 passed**, 1 warning, 8.52s |
| Backend lint | `cd backend && ./.venv/bin/python -m ruff check .` | All checks passed |
| Frontend lint | `cd frontend && pnpm lint` | Clean (eslint, no errors) |
| Frontend types | `cd frontend && pnpm type-check` | Clean (`tsc -b --noEmit`) |
| Frontend tests | n/a | **No frontend test runner exists** — only lint/type-check/build |

---

## Go Criteria Checklist

| # | Criterion (from prd.md) | Status | Evidence |
| --- | --- | --- | --- |
| G1 | All internal readiness workflows have visible failure/retry behavior | ✅ PASS | `readiness_service._persist_failure` (line 284) + `persist_application_failure` (line 363) write a failed `AgentStep`, failed `AgentRun`, sanitized `ApplicationFailureEnvelope` (category + next_action), and `artifact_generation_failed` timeline event in one transaction. Worker catches all exceptions → run flipped to `failed`. Frontend `FailurePanel.tsx` surfaces it. `jd_analysis_service` follows the same 6-step pattern. |
| G2 | External actions are impossible without approval | ✅ PASS | `approval_boundary.assert_action_approved()` (line 108) is the hard gate. `ApprovalBlockedError` reasons: `not_approved / revoked / stale / blocked / missing_approval / payload_hash_mismatch / source_stale`. No `/execute` or `/submit` endpoint exists — `test_no_external_execution_endpoint_exists` (test_application_actions.py:583) is a regression guard asserting 404. |
| G3 | Approval is bound to exact payload hash and source snapshot | ✅ PASS | `compute_payload_hash()` (approval_boundary.py:78) = sha256 over `action_type, target_platform, target_resource, selected_artifact_ids, outgoing_text, resume_file_reference`. `approve_action()` (approval_action_service.py:149) binds `approved_payload_hash`. `check_staleness()` (line 91) re-checks source_hash. Tests cover all 7 block conditions. |
| G4 | Application timeline can explain every action | ✅ PASS | `ApplicationRecord.timeline` is append-only JSON; `build_event()` (application_repo.py:211) stamps `id/type/at/actor/from_status/to_status/summary/metadata`. Events: `created`, `status_changed`, `failure`, `user_note`, `artifact_generated`, `artifact_generation_failed`, `action_previewed/approved/revoked/stale`. Status + timeline written in one flush (`update_status_with_event`). |
| G5 | One platform pilot is scoped narrowly | ✅ PASS (for review) | Pilot shape defined below in "First Platform Pilot Proposal". Target platform selection is a pilot-task decision, not a review blocker. |
| G6 | Pilot can run in dry-run/fill-only mode | ✅ PASS (by design) | The approval boundary + `assert_action_approved` guard are designed so a pilot can navigate/fill and stop before submit. Dry-run mode will be a pilot-task deliverable; the boundary already supports it. |

---

## No-Go Criteria Checklist

| # | Criterion (from prd.md) | Status | Evidence |
| --- | --- | --- | --- |
| N1 | Application statuses can be inconsistent | ❌ NOT MET (good) | `ApplicationStatus` StrEnum (10 values) + `TRANSITIONS` table (application_state.py:66-100) + `assert_transition()` raises `InvalidTransitionError`. Enforced at service (`application_service.update_application_status:159`) and API layer. Test: exhaustive all-pair disallowed-transition coverage (`test_disallowed_transitions_rejected`). |
| N2 | Failed internal workflows hide errors | ❌ NOT MET (good) | Every failure path persists a sanitized `ApplicationFailureEnvelope` with `category` + `next_action` on `ApplicationRecord.latest_error`, plus timeline `failure` event. Worker `fail_run()` (handlers.py:41) is the shared guard. Sanitization drops `raw/text/prompt/secret/token/cookie/credential/password/file_bytes` and values >256 chars. |
| N3 | Approval is just a boolean without payload binding | ❌ NOT MET (good) | `ApprovalRecord.approved_payload_hash` (schema/application_action.py:62-73) binds the exact payload. `assert_action_approved` blocks `payload_hash_mismatch`. |
| N4 | Duplicate external actions cannot be prevented | ❌ NOT MET (good) | Duplicate **internal** runs prevented via arq `_job_id` dedup (`runtime.enqueue_workflow:106`) + terminal-run skip (readiness_service:762) + duplicate-active 409 (applications.py:236-258). For **external** actions, the approval-boundary + payload-hash + `mark_stale` mechanism provides the equivalent guard; idempotency keys for platform submission are a pilot-task deliverable (see Hardening H1). |
| N5 | Platform failure categories lack handling rules | ❌ NOT MET (good) | Handling rules defined in this review's "Platform Failure Matrix" below, sourced from `design.md`. The readiness envelope already persists `platform` failure category (`ApplicationFailureCategory.platform`). |
| N6 | The proposed agent would perform autonomous bulk submission | ❌ NOT MET (good) | Explicitly rejected. Pilot is guided submit with mandatory user checkpoint before final submit (see Pilot Shape). |

No no-go criterion is triggered.

---

## Risk Table

| ID | Risk | Severity | Status | Action |
| --- | --- | --- | --- | --- |
| H1 | No idempotency key design for **external** platform actions (submit/HR-message). Internal runs have `idempotency_key`, but external actions have no equivalent yet — a retried platform submit could double-apply. | High | **Open — gates pilot** | Pilot task PRD must define external-action idempotency key (e.g. `{application_id}:{action_type}:{payload_hash}`) and a `submitted/unknown` terminal guard before any platform call. |
| H2 | `jd_analysis` worker has **no stale-source detection**, unlike the readiness worker. If a user edits the JD after a jd_analysis run is queued, the worker will not detect the drift and may produce an analysis on stale JD text. | High | **Open — gates pilot** | Add source-hash recompute + `stale_source` failure to `jd_analysis_service.run_resume_aware_jd_analysis_worker`, mirroring readiness_service:907-979. Low effort, high value before pilot relies on jd_analysis freshness. |
| R1 | Frontend has **no automated test runner** (no `vitest`/`jest` script). Only lint/type-check/build. UI regressions (stale detection, polling, failure panel) are not caught automatically. | Medium | Residual | Add a minimal vitest suite for the 6 readiness-panel components before or during the pilot. Does not block the review decision; track in pilot backlog. |
| R2 | `ApplicationRecord.timeline` is a JSON column, not a separate table — unbounded growth on long-lived applications could bloat the record row. | Low | Residual | Acceptable for pilot scale. Revisit if timeline grows beyond ~100 events per record. |
| R3 | No end-to-end integration test covering the full readiness loop (create application → generate 4 artifacts → approval preview → approve) in one test. Each layer is tested in isolation. | Low | Residual | The pilot task should add one E2E-style backend test. |

---

## Platform Failure Matrix

Sourced from `design.md`, confirmed against `ApplicationFailureCategory` /
`ApplicationFailureNextAction` enums.

| Failure | Required behavior | Mechanism status |
| --- | --- | --- |
| Login expired | stop, ask user to log in | Pilot task: detect + `platform` failure envelope + timeline event |
| CAPTCHA | pause, user handles challenge | Pilot task: pause → `approval_required`-like checkpoint |
| Selector drift | fail with diagnostic screenshot/log reference | Pilot task: `platform` failure + diagnostic captured in `latest_error` |
| Rate limit | stop, retry later/manual | Pilot task: `platform` failure, `next_action=manual_review` |
| Duplicate detected | stop, mark duplicate/manual review | Pilot task: ties to H1 idempotency key |
| Upload failure | retry if safe, otherwise manual | Pilot task: bounded retry + `platform` failure |
| Unknown final result | mark `unknown`, ask user to reconcile | Pilot task: new `submitted/unknown` handling on ApplicationRecord |

The internal envelope (category enum, next-action enum, sanitized envelope,
timeline event) already supports all of the above — the pilot task supplies the
platform-specific detection + classification.

---

## First Platform Pilot Proposal

### Shape: Guided Submit (semi-automatic)

```text
user selects application record
  -> agent dry-runs platform navigation + form fill (no final submit)
  -> agent stops before final submit, captures page state
  -> user reviews exact page/payload (bound to approval payload_hash)
  -> user explicitly approves via approval boundary
  -> agent executes final submit OR user manually submits
  -> timeline records submitted / unknown result
```

### Pilot PRD Must Include

1. **Target platform** — one, narrowly scoped (e.g. a single job board with
   stable form structure). Selection deferred to pilot task.
2. **Dry-run scope** — navigation + fill only, explicitly stops before submit.
3. **Login/session handling** — user-provided session; login-expiry detection;
   no credential storage (consistent with existing sanitization exclusions).
4. **Approval checkpoint** — reuse the existing approval boundary; bind to
   `compute_payload_hash` of the exact filled form; `assert_action_approved`
   before submit.
5. **External action idempotency** (resolves H1) — define
   `{application_id}:{action_type}:{payload_hash}` as the platform idempotency
   key; prevent duplicate submit.
6. **Failure matrix** — implement the 7 rows from the Platform Failure Matrix
   above with platform-specific detection.
7. **Rollback / manual reconciliation** — `unknown` final-result handling;
   timeline event with manual-review `next_action`; no silent overwrite.

### Explicitly Out of Scope for First Pilot

- Autonomous bulk submission across multiple applications.
- Auto-login / credential storage.
- HR messaging automation (second pilot iteration).
- Multi-platform support (one platform only).

---

## Evidence Sources (file references)

- State machine: `backend/app/services/application_state.py` (TRANSITIONS:66,
  assert_transition:108, compute_source_hash:166, build_failure_envelope:229)
- Application service: `backend/app/services/application_service.py`
  (update_application_status:159, duplicate-active guard:46-56)
- Readiness generation: `backend/app/services/readiness_service.py`
  (6 steps:76-81, stale detection:907-979, _persist_failure:284)
- JD analysis: `backend/app/services/jd_analysis_service.py`
  (worker:603, **no stale check** — see H2)
- Approval boundary: `backend/app/services/approval_boundary.py`
  (compute_payload_hash:78, assert_action_approved:108)
- Approval service: `backend/app/services/approval_action_service.py`
  (approve:149, revoke:226, mark_stale:322)
- Queue runtime: `backend/app/queue/runtime.py` (enqueue dedup:106),
  `backend/app/queue/handlers.py` (fail_run:41), `backend/app/queue/payloads.py`
  (frozen + idempotency_key:51-58)
- Models: `backend/app/db/models/models.py` (GeneratedArtifact:143,
  ApplicationRecord:163, AgentRun:240, AgentStep:262, ToolCall:278)
- Tests: `test_application_state.py` (544 lines, all-pair coverage),
  `test_applications_api.py` (688 lines), `test_readiness_artifacts.py`
  (1278 lines), `test_application_actions.py` (607 lines, incl. no-execute guard
  at line 583), `test_queue_runtime.py` (237 lines)
- Frontend: `frontend/src/pages/applications/ApplicationsPage.tsx` +
  `frontend/src/features/applications/*` (6 components)

---

## Conclusion

The readiness loop, failure envelope, and approval boundary are production-
grade for the internal surface and correctly stop at the external-action
boundary. The project is cleared to create a first platform-pilot task with the
two high-severity hardening items (H1, H2) as entry conditions in its PRD.
