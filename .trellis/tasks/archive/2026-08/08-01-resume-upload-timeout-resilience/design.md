# Resume Upload Timeout Resilience — Design

## Current Behavior

`frontend/src/api/client.ts` uses one shared axios instance with
`timeout: 15000`. `uploadResume()` uses that same instance.

`backend/app/api/v1/resumes.py::upload_resume` parses/stores the uploaded file
and then awaits:

```python
await resume_fact_service.extract_resume_facts(db, resume, version, gateway)
```

That means the upload response is blocked on a model-backed extraction workflow.
For PDF/DOCX, the request can exceed the frontend 15-second timeout, so the UI
reports a timeout even though the upload may have been saved.

## Target Behavior

Resume upload becomes a two-stage workflow:

1. **Upload stage (synchronous HTTP request)**:
   - validate extension and size;
   - parse file text;
   - save file and `ResumeVersion`;
   - write `_extraction.status = "pending"` for parsed raw text, or
     `"not_run"` for unsupported/empty raw text;
   - return `201` quickly with a usable `ResumeDetailOut`.

2. **Extraction stage (automatic follow-up workflow)**:
   - starts automatically after a parsed upload;
   - creates/updates a durable `AgentRun` / `AgentStep` trail;
   - transitions `_extraction.status` from `pending` to `running`, then
     `succeeded` or `failed`;
   - writes `parsed_facts.facts` on success;
   - leaves the uploaded resume visible and usable on failure.

The first implementation can use FastAPI `BackgroundTasks` or a small
in-process task launcher. It must open its own DB session and acquire its own
model gateway/settings instead of reusing the request-scoped `Session`.

## Data Contract

Extend `parsed_facts._extraction.status` to include:

- `pending`: upload saved, extraction scheduled but not started;
- `running`: extraction run started;
- `succeeded`: facts written;
- `failed`: extraction failed after upload;
- `not_run`: no extractable raw text / unsupported parser result.

Recommended `_extraction` shape:

```jsonc
{
  "status": "pending|running|succeeded|failed|not_run",
  "run_id": "<agent_run_id when known>",
  "extracted_at": "<terminal timestamp>",
  "prompt_version": "resume-fact-extraction-v1",
  "provider": "deepseek|fake",
  "model": "<model id>",
  "error": "<sanitized failure summary when failed>"
}
```

Existing rows remain compatible. Missing `_extraction` should be treated as
`not_run` or "not extracted yet" in UI copy.

## Backend Design

### Upload endpoint

`POST /api/v1/resumes` should no longer await the model extraction call.

For parsed raw text:

- persist the resume/version;
- write `_extraction.status = "pending"` before returning;
- schedule extraction using a post-response background task;
- return `ResumeDetailOut` immediately.

For unsupported/empty raw text:

- write `_extraction.status = "not_run"`;
- do not schedule extraction.

### Background extraction runner

Add a service entry point, for example:

```python
async def run_resume_fact_extraction_background(
    resume_id: str,
    version_id: str,
    user_id: str,
) -> None:
    ...
```

It should:

- create a fresh DB session;
- verify resume/version still belongs to `user_id`;
- set `_extraction.status = "running"` before calling the model;
- call the existing `extract_resume_facts(...)` orchestration, or a refactored
  variant that can mark running before the model call;
- commit `succeeded` / `failed` terminal status;
- log sanitized IDs/errors only.

Do not reuse the request-scoped `db` session inside the background task.

### Existing extraction service

`resume_fact_service.extract_resume_facts(...)` already handles success,
failure, `not_run`, and sanitized `AgentRun`/`AgentStep` persistence. It may be
refactored, but preserve these existing contracts:

- upload-time model failure does not turn into a file upload failure;
- explicit re-extract (`raise_on_failure=True`) can still return 502;
- no raw resume text or prompt appears in run/step metadata.

### API read surface

Existing `GET /resumes/{id}` can be used for polling because it returns
`latest_version.parsed_facts`. If a dedicated run detail link is useful, the UI
can use `_extraction.run_id` with `/agent-runs/{run_id}/detail`.

## Frontend Design

### API client

Do not rely on increasing the global axios timeout as the fix. Optional:
introduce per-endpoint timeout constants later, but the core fix is decoupling
model extraction from upload.

### Upload UX

On successful upload:

- show the resume as uploaded immediately;
- display extraction status from `parsed_facts._extraction.status`;
- if `pending` or `running`, poll `GET /resumes/{id}` every few seconds until
  `succeeded`, `failed`, or `not_run`;
- keep a manual "重新解析" action for failed status.

If the upload HTTP request itself times out or fails, normalize the axios error
message into user-facing copy such as "上传请求超时，请刷新简历列表确认是否已保存"
instead of exposing raw `timeout of 15000ms exceeded`.

## Tests

Backend:

- upload with a slow extraction gateway/task returns `201` before extraction
  completes or without awaiting the model call;
- parsed uploads initially return `_extraction.status="pending"` or `running`;
- background runner eventually writes `facts` and `succeeded`;
- background failure writes `failed` and a failed `AgentRun`;
- unsupported/empty raw text remains `not_run`;
- explicit re-extract still works and remains user-scoped;
- no raw resume text leaks into `AgentRun`/`AgentStep`.

Frontend:

- upload success renders pending/running extraction state;
- detail page polling updates to succeeded/failed;
- timeout errors are normalized and do not expose raw axios wording as the main
  message;
- retry button remains available for failed extraction.

## Trade-offs

- FastAPI `BackgroundTasks` is not a production queue, but it removes the
  immediate UX failure and keeps the workflow observable for MVP.
- A later worker/Redis queue can replace the launcher without changing the UI
  contract if the status values and `AgentRun` trail stay stable.
