# Resume Upload Timeout Resilience

## Goal

Fix resume upload failures where PDF/DOCX uploads surface an error containing
`15000ms`, while preserving the product requirement that uploaded resumes are
automatically parsed into durable resume facts.

## Root Cause

The `15000ms` string comes from the frontend API client timeout:
`frontend/src/api/client.ts` creates the shared axios client with
`timeout: 15000`.

The resume upload endpoint currently performs all of this inside the single
`POST /api/v1/resumes` request:

1. read the uploaded file;
2. parse text from PDF/DOCX (`pdfplumber` / `python-docx`);
3. persist `Resume` and `ResumeVersion`;
4. call `resume_fact_service.extract_resume_facts(...)`, which makes a model
   call through `ModelGateway`;
5. return the response only after extraction finishes.

PDF/DOCX parsing is slower than `.txt`, and the model extraction call can add
network latency. When the combined request exceeds 15 seconds, axios aborts the
request and shows a timeout error even though the backend may still be working
or may already have saved rows. The backend DeepSeek client uses a 60-second
timeout, so the observed `15000ms` is a frontend request timeout, not the
provider timeout.

## Requirements

- Uploading `.pdf` and `.docx` must not depend on the model extraction finishing
  before the upload request returns.
- `POST /api/v1/resumes` must still save the file, create `Resume` +
  `ResumeVersion`, and return a usable resume detail when text parsing
  succeeds.
- Resume fact extraction must still start automatically after upload, but it
  must be represented as a visible, auditable follow-up workflow rather than
  hidden synchronous request work.
- The response and detail view must clearly show extraction status:
  `pending` / `running` / `succeeded` / `failed` / `not_run`.
- Failed or slow extraction must not make the upload itself look failed after
  the file and raw text were saved.
- The frontend must not show the raw axios timeout message as the primary user
  error for upload. If extraction is still running, the UI should say the resume
  is uploaded and facts are still being extracted.
- Users must be able to retry extraction from the resume detail page.
- AgentRun / AgentStep sanitization must remain intact: no raw resume text, raw
  prompt, or API key in run/step metadata.
- User ownership boundaries must remain scoped through `get_current_user` and
  user-owned resume/version checks.

## Acceptance Criteria

- [ ] Uploading a PDF/DOCX whose extraction takes longer than 15 seconds still
      returns a successful upload response within the frontend timeout budget.
- [ ] Upload response includes parser status and extraction status without
      waiting for the model call to finish.
- [ ] Automatic extraction creates or updates an auditable `AgentRun` and
      eventually writes `parsed_facts.facts` plus `_extraction.status`.
- [ ] The resume detail UI displays `pending/running/succeeded/failed/not_run`
      extraction states and refreshes/polls until a terminal state.
- [ ] Extraction failure after upload leaves the uploaded resume usable and
      visible, with a failed run that can be inspected and retried.
- [ ] The explicit re-extract endpoint still works and remains user-scoped.
- [ ] Tests cover slow extraction upload, successful async extraction, failed
      async extraction, retry, unsupported format `not_run`, no raw resume
      leakage, and cross-user access.

## Notes

- Raising the global axios timeout alone is not an acceptable fix. It may hide
  the symptom for small files but keeps model latency coupled to file upload.
- A production queue can come later. For this MVP, an in-process background
  task or lightweight internal queue is acceptable if it persists status before
  doing model work and is easy to replace later.
