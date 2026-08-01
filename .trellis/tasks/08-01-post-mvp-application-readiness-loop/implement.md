# Implementation Plan

This is a planning artifact only. Do not start implementation until the user
explicitly asks to implement a child task.

## Phase 0 — Reliability Contracts First

Goal: define reusable contracts before adding visible features.

Tasks:

- Define shared failure envelope for application-readiness operations.
- Define application status enum and allowed transitions.
- Define freshness/source-hash rules for readiness artifacts.
- Define duplicate active-run policy per job/resume/operation.
- Define approval payload hash contract for future external actions.

Validation:

- Unit tests for status transition table.
- Unit tests for source-hash changes when job/resume/profile/prompt changes.
- Contract tests that failure envelopes do not include raw resume/JD text.

Rollback point:

- Stop here if status/failure contracts feel wrong; do not build UI on a shaky
  state machine.

## Phase 1 — Application Records Center

Goal: make the placeholder applications API real, but keep external actions
manual.

Deliverables:

- Backend CRUD/list/detail for application records.
- Application status transitions with validation.
- Timeline event append helper.
- User-scoped access with 404 on cross-user resources.
- Frontend application list/detail view.
- Job detail entry point: "创建投递记录".

Failure focus:

- Duplicate application record for same job/resume should be prevented or
  merged deliberately.
- Invalid status transitions should return clear 422 errors.
- Timeline write failures must not leave misleading status if the operation did
  not complete.

Acceptance:

- User can create an application record from a job.
- User can move status manually through allowed states.
- Timeline explains every state change.
- Cross-user access tests pass.

## Phase 2 — Readiness Artifact Generation

Goal: generate usable materials, but every run remains auditable and retryable.

Deliverables:

- `hr_opening_message` workflow.
- `resume_rewrite_snippet` workflow.
- `skill_gap_plan` workflow.
- `interview_prep` workflow.
- Shared artifact result envelope.
- Link artifacts to application record and source hash.

Failure focus:

- Queue/model/schema failures produce `failed` state and retry affordance.
- Existing good artifacts remain visible after a failed retry.
- Stale artifacts are flagged when source data changes.
- Duplicate active generation is rejected or attached to existing active run.

Acceptance:

- From one application record, user can generate all required materials.
- Each generated artifact has prompt/model/source/run provenance.
- Invalid model output never becomes a successful artifact.
- Retry creates a clean audit trail.

## Phase 3 — Readiness Panel UX

Goal: make failure and next action obvious on the job/application detail page.

Deliverables:

- Readiness checklist component.
- Artifact cards with freshness and generation status.
- AgentRun trace links or embedded step timeline.
- Failure panel using the shared failure envelope.
- "retry", "edit source", "choose resume", "pause", and "mark manually done"
  actions as appropriate.

Failure focus:

- No indefinite loading states.
- Every failed row/card shows the failed operation, retryability, and next
  action.
- Stale materials are visible but clearly marked.

Acceptance:

- User can understand what is missing before applying.
- User can recover from failed generation without losing previous artifacts.
- User can pause and resume an application workflow.

## Phase 4 — Approval Boundary

Goal: prepare for automation without doing platform automation yet.

Deliverables:

- Approval-required action preview model.
- Payload hash and source snapshot.
- Approve/revoke approval endpoints.
- UI confirmation surface showing exact action payload.
- Expire approval when payload/source changes.

Failure focus:

- Approval cannot apply to a changed payload.
- Approval can be revoked before execution.
- Actions without approval cannot be executed by future platform tools.

Acceptance:

- System can represent "user approved this exact planned action".
- If source data changes, approval becomes stale and requires reapproval.
- Tests prove unapproved actions are blocked.

## Phase 5 — Automated Agent Readiness Review

Goal: decide whether the project is ready for a first platform pilot.

Review checklist:

- Are application statuses reliable and recoverable?
- Can every internal failure be explained to the user?
- Are successful and failed generated artifacts auditable?
- Is idempotency defined for future external actions?
- Can the user approve an exact payload?
- Is there a selected first platform?
- Do we have dry-run behavior before submit?
- Do we know how to handle login expiry, CAPTCHA, selector drift, duplicate
  application, rate limit, network failure, and unknown final result?

Decision:

- If all checks pass, start a new task for a semi-automatic guided submit agent.
- If any check fails, create a hardening task before platform automation.

## Suggested Child Task Split

1. `application-state-machine-and-failure-envelope`
   Build status transitions, timeline helper, failure envelope, and source hash
   contracts.

2. `application-records-center`
   Implement application CRUD/list/detail and manual state management.

3. `readiness-artifact-generation`
   Generate HR opening message, resume rewrite snippets, skill-gap plan, and
   interview prep as async artifacts.

4. `readiness-panel-ux`
   Add job/application detail UI for materials, status, failures, stale
   warnings, and retry actions.

5. `approval-boundary-for-external-actions`
   Add durable approval payload preview/hash/revoke mechanics.

6. `automation-readiness-review`
   Produce the go/no-go decision for the first semi-automatic platform agent.

## Validation Commands Once Implemented

```bash
cd backend && pytest
cd backend && ruff check .
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 ./.trellis/scripts/task.py validate <task>
git diff --check
```

## Explicit Non-Goals

- Do not implement browser automation in these child tasks.
- Do not send applications automatically.
- Do not send HR messages automatically.
- Do not hide external-action execution behind a generic "agent run".

