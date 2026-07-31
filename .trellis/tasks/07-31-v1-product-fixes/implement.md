# 7.31 First Version Product Fixes Implementation Plan

## Execution Order

1. Complete `07-31-archive-previous-planning-artifacts`.
   - Verify which predecessor tasks are complete.
   - Archive completed predecessor tasks through Trellis.
   - Record why anything remains unarchived.

2. Complete `07-31-agent-run-observability-result-visibility`.
   - Fix frontend result retrieval for existing persisted analyses.
   - Expose ordered run steps and status.
   - Add tests for result visibility and user-scoped run detail.

3. Complete `07-31-resume-upload-auto-profile-parsing`.
   - Define structured resume/profile extraction schema.
   - Trigger extraction after upload.
   - Persist facts and expose profile draft state.

4. Complete `07-31-structured-profile-text-fields`.
   - Replace vague constraints editing with explicit text fields.
   - Keep compatibility with existing stored profile data.

5. Complete `07-31-jd-paste-auto-parsing`.
   - Add paste-first JD parse flow.
   - Let users review/edit parsed fields before saving.

## Validation Baseline

Run the appropriate subset per child task, and the full suite before merging the
parent:

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate <task-name>
```

## Risk Points

- Agent step visibility must not leak raw resume text, full prompts, or API
  keys.
- Existing fake model provider behavior must keep tests offline.
- User ownership checks must remain consistent across jobs, resumes, analyses,
  artifacts, and agent runs.
- Frontend result loading should not rely only on in-memory state from the run
  button click.
