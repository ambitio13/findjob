# Application Records Center

## Goal

Turn the existing placeholder applications route into a real application record
center where users can track one job/resume application from planning through
preparation, approval, submission, and follow-up.

This task is still manual-first. It must not submit to external platforms.

## Requirements

- Implement application record create/list/detail/update-status APIs.
- Scope all records to the current user through owned job/resume relationships.
- Persist timeline events for every meaningful state change.
- Bind records to `job_id` and optional `resume_version_id`.
- Store latest failure envelope and latest relevant agent run id when present.
- Expose enough data for frontend list/detail views.
- Prevent or intentionally merge duplicate records for the same job/resume.

## Failure Handling Requirements

- Cross-user job/resume access returns 404.
- Invalid status transitions return 422 with a stable message.
- Duplicate active record creation returns 409 or returns the existing record,
  but behavior must be consistent and tested.
- Timeline append and status update must be transactional.
- If resume version is missing parsed text, record can exist but cannot enter
  `preparing` until the user chooses a usable resume.

## Acceptance Criteria

- [ ] `GET /applications` returns paginated current-user records.
- [ ] `POST /applications` creates a record for an owned job/resume.
- [ ] `GET /applications/{id}` returns detail with timeline.
- [ ] Status update endpoint enforces the transition table.
- [ ] Timeline events explain create/update/failure/manual transitions.
- [ ] Cross-user and duplicate cases are tested.
- [ ] No external platform side effects are implemented.

