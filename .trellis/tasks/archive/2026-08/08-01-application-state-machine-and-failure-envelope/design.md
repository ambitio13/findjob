# Design

## Boundary

This task should create reusable domain contracts, not UI surfaces and not
artifact generation. Later tasks import these contracts.

## Proposed Modules

- `backend/app/schemas/application.py`
  - `ApplicationStatus`
  - `ApplicationFailureCategory`
  - `ApplicationFailureNextAction`
  - `ApplicationFailureEnvelope`
  - `ApplicationSourceSnapshot`
- `backend/app/services/application_state.py`
  - `can_transition(from_status, to_status)`
  - `assert_transition(from_status, to_status)`
  - `build_source_snapshot(...)`
  - `build_failure_envelope(...)`

## Transition Table

```text
planned -> preparing | paused
preparing -> materials_ready | failed | paused
failed -> preparing | paused
materials_ready -> approval_required | preparing | paused
approval_required -> approved | preparing | paused
approved -> submitted | approval_required | failed | paused
submitted -> interviewing | rejected | paused
paused -> planned | preparing
interviewing -> rejected | paused
rejected -> planned
```

## Source Snapshot

Use a stable hash over identifiers and versions, not raw text:

```json
{
  "job_id": "...",
  "job_updated_at": "...",
  "resume_version_id": "...",
  "profile_updated_at": "...",
  "prompt_versions": {
    "hr_opening_message": "..."
  },
  "source_hash": "sha256:..."
}
```

## Duplicate Active Operation Rule

For one application record, reject duplicate active work if an operation with
the same `operation_type`, `job_id`, `resume_version_id`, and `source_hash` is
already `queued` or `running`.

If the active run exists, return the existing run reference instead of starting
another run when the API contract naturally supports that. Otherwise return
409 with the active run id.

## Failure Safety

Only safe metadata goes into failure envelopes:

- IDs;
- counts;
- source hash;
- operation type;
- stable error code;
- short user-facing message.

Never include:

- raw resume text;
- raw JD text;
- model prompt content;
- uploaded file bytes;
- cookies, tokens, or platform credentials.

