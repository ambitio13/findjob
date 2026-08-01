# Async Job UX and Observability

## Goal

Create a consistent frontend experience for queued agent work across JD parsing, resume extraction, and JD analysis: non-blocking submissions, visible progress, retry paths, and result hydration.

## Requirements

- Provide shared UI/state helpers for async `AgentRun` polling.
- Normalize lifecycle copy and tag colors across workflows.
- Show queued/running state without trapping users in modal-level blocking spinners.
- Show failed state with sanitized reason, retry action, and trace visibility.
- Hydrate succeeded results into the relevant form/page automatically.
- Keep UI compact and work-focused; development-stage detail is acceptable, but process must be clear.

## Acceptance Criteria

- [ ] Shared frontend hook or utility handles polling until terminal state.
- [ ] Shared status rendering covers queued/running/succeeded/failed/not_run.
- [ ] JD paste modal, resume detail, and job detail use consistent async state copy.
- [ ] Users can inspect process metadata for failed or running runs.
- [ ] Retry buttons are available where retry is supported by backend.
- [ ] No action button waits silently on a model call without progress feedback.
- [ ] Frontend lint/type-check/build pass.

## Dependencies

- Depends on at least one migrated workflow to avoid speculative UI-only work.
- Should close after the three workflow migration tasks.
