# Async Resume Fact Extraction

## Goal

Move resume fact extraction to the shared queue runtime so upload and re-extract both return quickly, expose status, and run model work in a worker process instead of the FastAPI request lifecycle.

## Current Evidence

- `POST /api/v1/resumes` uses `BackgroundTasks` to run extraction after saving the resume.
- `POST /api/v1/resumes/{resume_id}/versions/{version_id}/extract` still awaits `extract_resume_facts(...)` synchronously.
- `ResumeVersion.parsed_facts._extraction.status` already supports `pending`, `running`, `succeeded`, `failed`, `not_run`.

## Requirements

- Upload must keep returning saved resume details quickly after parser/file persistence.
- Upload must enqueue extraction when raw text exists and set status `pending` or `queued`.
- Re-extract must enqueue a fresh extraction run instead of blocking on the model.
- The details page must poll until terminal status and keep retry available on failure.
- Worker must open its own DB session and build its own gateway.
- No raw resume content may be stored in run/step metadata.

## Acceptance Criteria

- [ ] `backend/app/api/v1/resumes.py` no longer imports or uses FastAPI `BackgroundTasks`.
- [ ] Upload enqueues resume fact extraction through the shared queue runtime.
- [ ] Re-extract returns immediately with queued/running state.
- [ ] Worker success writes typed `facts` and `_extraction.status="succeeded"`.
- [ ] Worker failure writes `_extraction.status="failed"` plus a failed `AgentRun`.
- [ ] Detail page polling and retry still work.
- [ ] Tests cover upload enqueue, re-extract enqueue, worker success/failure, cross-user protection, unsupported `not_run`, and no raw resume leakage.

## Dependencies

- Depends on `08-01-queue-runtime-foundation`.
