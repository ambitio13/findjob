# Application State Machine and Failure Envelope

## Goal

Define and implement the durable state and failure contracts that every
application-readiness workflow will use before any user-facing material
generation or platform automation is built.

This task is the reliability foundation. It should answer: what state is the
application in, what failed, whether retry is safe, and what the user can do
next.

## Requirements

- Add a typed application status set and allowed transition table.
- Add a shared failure envelope shape for readiness operations.
- Add source-hash / freshness rules for job + resume version + profile +
  prompt-version inputs.
- Add duplicate active-operation rules for job/resume/operation combinations.
- Keep raw JD text, raw resume text, raw prompts, and secrets out of failure
  payloads and casual logs.
- Keep contracts backend-owned; frontend may mirror display labels later.

## Recommended Statuses

- `planned`
- `preparing`
- `materials_ready`
- `approval_required`
- `approved`
- `submitted`
- `failed`
- `paused`
- `rejected`
- `interviewing`

## Failure Envelope

Every failed readiness operation should be representable as:

```json
{
  "category": "queue | model | validation | data | user_action | platform | unknown",
  "code": "stable_error_code",
  "message": "safe user-facing message",
  "retryable": true,
  "next_action": "retry | edit_source | choose_resume | reapprove | manual_review",
  "agent_run_id": "run_id",
  "source_ids": {},
  "occurred_at": "timestamp"
}
```

## Acceptance Criteria

- [ ] Application statuses are represented by a constrained backend type.
- [ ] Invalid status transitions are rejected by tests.
- [ ] Failure envelope model validates category, code, retryability, and next
  action.
- [ ] Source hashes change when job, resume version, profile, or prompt version
  changes.
- [ ] Failure envelope tests assert no raw resume/JD text is persisted.
- [ ] Duplicate active operation behavior is documented and test-covered.

