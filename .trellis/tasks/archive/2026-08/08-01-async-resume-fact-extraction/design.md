# Design

## Backend

Replace `_schedule_extraction` / `_run_extraction_task` in `resumes.py` with queue enqueue:

1. Upload parses and saves file/version.
2. If raw text is empty, write `_extraction.status="not_run"`.
3. If raw text exists, write `_extraction.status="pending"` and create/enqueue a resume extraction payload.
4. Return `ResumeDetailOut`.

For re-extract:

1. Validate resume/version ownership.
2. Mark `_extraction.status="pending"` and enqueue a fresh run.
3. Return detail immediately.

## Worker

Worker handler:

- Load resume/version by IDs and user ownership.
- Mark status `running`.
- Call a refactored `extract_resume_facts` using an existing or newly-created `AgentRun`.
- Persist terminal status and sanitized steps.

## Frontend

`ResumeDetailPage` already polls `GET /resumes/{id}` while status is non-terminal. Preserve this and adjust copy if `queued` is introduced.

Upload modal should continue to show success once the file is saved, not after extraction.

## Compatibility

If the foundation uses `AgentRun.status="queued"` but resume `_extraction.status` keeps `pending`, map them intentionally:

- `AgentRun.queued` -> `_extraction.pending`
- `AgentRun.running` -> `_extraction.running`
