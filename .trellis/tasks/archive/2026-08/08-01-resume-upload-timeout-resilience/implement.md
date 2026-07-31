# Resume Upload Timeout Resilience — Implementation Plan

## Phase A — Backend Contract and Runner

1. Update resume extraction status handling.
   - Add support for `_extraction.status = "pending"` and `"running"`.
   - Keep existing `succeeded`, `failed`, and `not_run`.
   - Add small helpers if needed so status writes preserve `_parser` telemetry.

2. Change `POST /api/v1/resumes`.
   - Remove the awaited inline model call from the upload response path.
   - After saving a parsed resume version, write `_extraction.status="pending"`.
   - Schedule automatic extraction after response.
   - For unsupported/empty raw text, write `not_run` and do not schedule.
   - Return `ResumeDetailOut` immediately after file/text persistence.

3. Add a background extraction entry point.
   - Use a fresh DB session inside the task.
   - Re-load and ownership-check resume/version by `resume_id`, `version_id`,
     `user_id`.
   - Mark `_extraction.status="running"` before model work starts.
   - Reuse or refactor `resume_fact_service.extract_resume_facts(...)` for the
     actual AgentRun/AgentStep workflow.
   - On exception, persist sanitized `failed` status and log IDs only.

4. Preserve explicit re-extract behavior.
   - `POST /resumes/{resume_id}/versions/{version_id}/extract` remains
     synchronous as the primary action.
   - It may still return 502 after persisting a failed run.

## Phase B — Backend Tests

Add or update tests in `backend/app/tests/test_resume_upload.py` and
`backend/app/tests/test_resume_fact_service.py`:

- Upload with parsed raw text returns `201` and `_extraction.status` is
  `pending` or `running` before the model extraction result is required.
- A slow/stubbed extraction path does not block upload response on the model
  call.
- Background runner success writes `parsed_facts.facts`,
  `_extraction.status="succeeded"`, and an auditable run id.
- Background runner model failure writes `_extraction.status="failed"` and a
  failed `AgentRun` / `AgentStep`.
- Unsupported `.rtf` remains `not_run`.
- Cross-user version access is still 404.
- Sanitization test confirms no raw resume token appears in run/step metadata.

## Phase C — Frontend Upload and Detail UX

1. Update frontend types.
   - Include `pending` and `running` in `ResumeExtractionStatus`.
   - Keep optional fields defensive for older rows.

2. Update upload flow.
   - Treat upload success as "file saved"; do not require facts to be present.
   - Navigate/render the uploaded resume immediately.
   - Normalize axios timeout errors into user-facing copy that suggests checking
     the resume list/detail instead of showing raw `15000ms`.

3. Update `ResumeDetailPage`.
   - Render `pending` and `running` states.
   - Poll `getResume(id)` while status is `pending` or `running`.
   - Stop polling on `succeeded`, `failed`, or `not_run`.
   - Keep "重新解析" available for failed status.

## Phase D — Full Validation

Run:

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate 08-01-resume-upload-timeout-resilience
```

Manual smoke:

- Upload a PDF.
- Upload a DOCX.
- Confirm upload response succeeds before model extraction must finish.
- Confirm resume detail shows extraction progress and then facts or failed
  status.
- Confirm retry works after a simulated model failure.

## Risk Points

- Do not reuse the request-scoped SQLAlchemy `Session` inside a background task.
- Do not let background exceptions disappear silently; persist `failed` status.
- Do not remove the automatic extraction requirement.
- Do not solve this only by increasing frontend timeout.
- Keep run/step metadata sanitized.

## Rollback

If the background runner is unstable, keep the upload decoupling and expose a
clear "开始解析/重新解析" action as a temporary fallback, but do not return to
blocking the upload request on the model call.
