# Readiness Panel UX

## Goal

Give the user a clear job/application detail surface that explains readiness,
missing materials, current async work, failures, stale artifacts, and the next
safe action.

The UX goal is operational clarity, not visual flourish.

## Requirements

- Add an application readiness panel on job or application detail.
- Show selected job, resume version, application status, and readiness package.
- Show artifact cards for HR message, resume snippets, skill-gap plan, and
  interview prep.
- Show active AgentRun status and step trail when a generation is running.
- Show failed operations using the shared failure envelope.
- Show stale artifact warnings when source hashes differ.
- Provide explicit actions: generate, retry, choose resume, edit source, pause,
  resume, mark manually submitted.

## Failure UX Requirements

- No indefinite spinners.
- Every failed operation shows retryability and next action.
- Failed retry does not hide previous successful materials.
- Stale artifacts remain readable but clearly marked.
- Duplicate active operation should point to the current active run.
- Manual status changes should produce visible timeline entries.

## Acceptance Criteria

- [ ] User can tell whether an application is ready, blocked, failed, or stale.
- [ ] User can retry a failed generation from the panel.
- [ ] User can inspect or link to AgentRun details.
- [ ] User can pause/resume an application workflow.
- [ ] UI text does not imply AI output is verified truth.
- [ ] Frontend lint/type-check/build pass.

