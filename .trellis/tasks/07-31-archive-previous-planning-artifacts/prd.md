# Archive Previous Planning Artifacts

## Goal

Close and archive completed predecessor planning/task artifacts before starting
v1 fixes, so new work does not inherit stale active-task pointers or ambiguous
"already done but still active" state.

## Requirements

- Inspect current Trellis tasks and archived tasks.
- Confirm whether these predecessor tasks are complete or should remain active:
  - `07-31-mvp-project-skeleton`
  - `07-31-user-profile-preferences`
  - `07-31-resume-upload-parsing-foundation`
- Archive completed predecessor tasks through `task.py archive`.
- Keep the new parent task `07-31-v1-product-fixes` and its children active in
  planning.
- Do not modify product code in this task.

## Acceptance Criteria

- [ ] Completed predecessor tasks are moved under `.trellis/tasks/archive/`.
- [ ] No stale current-task pointer remains on an archived predecessor task.
- [ ] Any predecessor task left unarchived has a written reason in this PRD or
  session journal.
- [ ] `git status` is clean after the archive/journal commits.

## Notes

- This is a housekeeping task and should remain PRD-only unless archiving uncovers
  a Trellis metadata problem.
