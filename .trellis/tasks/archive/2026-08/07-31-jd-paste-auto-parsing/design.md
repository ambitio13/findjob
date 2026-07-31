# JD Paste Auto Parsing — Design

## Context

The current JD entry flow (`backend/app/api/v1/jobs.py:63-82`,
`frontend/src/features/jobs/JobCreateModal.tsx`) requires the user to manually
fill `company`, `title`, `location`, `salary_range`, `direction`, and `jd_raw`
before a `JobPosting` is created. There is no model-backed parsing: the
`JobPosting.jd_normalized` JSON column exists (`models.py:113`) but nothing in
the codebase reads or writes it.

The desired UX is paste-first: copy a JD from a platform, let the system parse
structured fields, then let the user correct fields before saving. This task
adds a parse-then-create flow that mirrors the two existing "raw text →
ModelGateway parse → structured fields → persist + audit" workflows:

- `resume_fact_extraction` (`agents/resume_fact_executor.py` +
  `services/resume_fact_service.py`) — the closest analog: parse raw text into
  typed fields, persist into a JSON column, record a six-step `AgentRun`.
- `resume_aware_jd_analysis` (`agents/jd_analysis_executor.py` +
  `services/jd_analysis_service.py:256-528`) — the original executor + service
  + six-step run template.

## Architecture and Boundaries

### New component: JD paste parsing

A new executor + service pair mirroring the resume-fact pattern:

- `backend/app/agents/jd_paste_executor.py`
  - `JdPasteExecutor(gateway: ModelGateway)` — injected, no direct provider
    calls (mirrors `resume_fact_executor.py:72-81`).
  - `build_prompt(raw_jd, platform_hint) -> JdPastePromptResult` — delegates to
    `agents/prompts/jd_paste.py`.
  - `async call_model(messages) -> ChatResponse` via `gateway.chat`
    (`ChatRequest(messages, temperature=0.1)`, mirroring
    `resume_fact_executor.py:107-110`).
  - `validate(response) -> JdPasteFactsModelOutput` — `json.loads` then
    `JdPasteFactsModelOutput.model_validate`, raising
    `JdPasteValidationError(kind="json"|"schema", ..., request_id=...)`
    (mirrors `resume_fact_executor.py:112-150`).

- `backend/app/agents/prompts/jd_paste.py`
  - `PROMPT_VERSION = "jd-paste-parsing-v1"` (mirrors
    `prompts/resume_fact.py:22`).
  - `JD_RAW_CAP = 8_000` constant (mirrors the JD-analysis cap at
    `prompts/jd_analysis.py:24-25`).
  - `_SYSTEM_PROMPT` — extraction assistant, no fabrication, mark uncertain
    fields, output a single JSON object, no markdown (mirrors
    `prompts/resume_fact.py:41-62`).
  - `build_jd_parse_messages(raw_jd, platform_hint) -> JdPastePromptResult` —
    system + user `ChatMessage`; user message inlines the output JSON schema
    and the capped raw JD under `## SOURCE` / `## JD TEXT` / `## REQUIRED
    OUTPUT` sections (mirrors `prompts/resume_fact.py:72-140`).
  - `_truncate(text, cap) -> tuple[str, dict]` — returns truncated text +
    `{truncated: bool, dropped_chars: int}` for provenance (mirrors
    `prompts/resume_fact.py:65-69`).

- `backend/app/services/jd_parse_service.py`
  - `WORKFLOW_TYPE = "jd_paste_parsing"` (mirrors
    `resume_fact_service.py:55`).
  - Six step-name constants: `load_context`, `build_prompt_context`,
    `call_model`, `validate_model_output`, `persist_outputs`, `complete_run`
    (mirrors `resume_fact_service.py:62-67`).
  - `parse_jd(db, current_user, raw_jd, platform_hint, gateway) ->
    JdParseOutcome` orchestration mirroring
    `resume_fact_service.extract_resume_facts`:
    1. `load_context` — record sanitized input metadata after the API boundary
       has already rejected blank `raw_jd` with 422. The service should not
       create special skipped rows for invalid requests.
    2. Create `AgentRun` (`workflow_type=WORKFLOW_TYPE`, `status="running"`,
       `job_id=None` — the job does not exist yet).
    3. `build_prompt_context` — sanitized step result (raw_jd_len, cap,
       truncation, prompt_version, platform_hint); never raw JD text.
    4. `call_model` — sanitized (provider/model/request_id/latency_ms/usage).
    5. `validate_model_output` — sanitized (validation status, kind on
       failure).
    6. `persist_outputs` — no `JobPosting` to write; the parse result lives
       only in the `AgentRun.result` + the returned `JdParseOutcome`. Step
       result records `field_count` and `uncertain_count`, not the fields
       themselves (the full parsed fields are in the API response, not in
       step metadata).
    7. `complete_run` — `AgentRun.status="succeeded"`, `result={field_count,
       uncertain_count, prompt_version, provider, model}`.
  - `_fail_run(...)` helper mirroring `resume_fact_service.py:136-172`:
    persist failed step + failed run (sanitized error only), commit, return
    `JdParseOutcome(status="failed", run=run, fields=JdPasteFactsModelOutput())`.
  - Because parsing is an auxiliary preview action (not the primary create),
    model/provider/schema failures are recoverable: the route handler returns
    HTTP 200 with a failed run and empty typed fields. Request validation
    failures (blank `raw_jd`) remain normal FastAPI/Pydantic 422 responses and
    create no `AgentRun`.

### `JdParseOutcome` contract

```python
@dataclass
class JdParseOutcome:
    status: Literal["succeeded", "failed"]
    run: AgentRun
    fields: JdPasteFactsModelOutput # empty model on failed parse
    extraction: JdParseExtraction   # parse provenance → jd_normalized._extraction
    raw_jd: str                     # echoed back for the form to re-submit
```

`JdParseDraftFields` mirrors `JdPasteFactsModelOutput` at the API/type level
(all fields optional / default-empty). Returning a typed empty object on
failure keeps the frontend contract stable. `extraction` carries the
parse-provenance block (`run_id`, `parsed_at`, `prompt_version`, `provider`,
`model`) so the frontend can persist it into `jd_normalized._extraction` on
save without an extra round-trip to the run detail.

### Parse endpoint

- `POST /api/v1/jobs/parse` in `backend/app/api/v1/jobs.py`:
  - Request: `JdParseRequest` (`raw_jd: str`, `platform: str | None = None`)
    with `raw_jd` trimmed/non-blank validation (`422` on blank).
  - Injects `gateway: ModelGateway = Depends(get_model_gateway_dep)` (mirrors
    `resumes.py:44`).
  - Calls `jd_parse_service.parse_jd(db, current_user, raw_jd, platform,
    gateway)`.
  - Response: `JdParseResponse` — `{status, run: {id, status, error}, fields:
    JdParseDraftFields, extraction: JdParseExtraction, raw_jd}`.
  - Model/provider/schema failures return HTTP 200 with `status="failed"` and
    a failed run; request validation failures return 422 before service entry.

### Create endpoint adjustment

- Extend `JobCreate` (`schemas/api.py:31-38`): add optional
  `jd_normalized: dict[str, Any] | None = None`.
- Extend `JobOut` (`schemas/api.py:41-42`): add `jd_normalized: dict[str, Any]
  | None = None` so the detail page can display parsed metadata.
- `create_job` (`jobs.py:63-82`): pass `jd_normalized=payload.jd_normalized`
  into the `JobPosting(...)` constructor. No other change.
- No `PATCH` endpoint in this task (create-only scope).

### Repository

- Create `backend/app/db/repositories/job_repo.py` to收口 Job CRUD (currently
 裸查询 in `jobs.py`), with `create`, `get_by_id`, `list_for_user`. The parse
  flow does not touch `job_repo` (no job is created during parse); only the
  adjusted `create_job` uses it. This is a light refactor to align with the
  project's Repository Rules (other repos: `job_analysis_repo.py`,
  `resume_repo.py`, `agent_run_repo.py`).

### Fake gateway routing

- `backend/app/models_gateway/fake.py`: add
  `_JD_PASTE_MARKER = "jd paste parsing assistant"` (mirrors
  `_RESUME_FACT_MARKER` at `fake.py:33`). In `chat()`, add a branch that
  returns a deterministic schema-valid `_JD_PASTE_FAKE_OUTPUT` when the system
  prompt contains the marker (mirrors `fake.py:57-72`).

## Data Flow and Contracts

### `jd_normalized` shape (the durable contract)

`JobPosting.jd_normalized` (JSON, `models.py:113`) stores the parsed structured
result when the job is created from a parse:

```jsonc
{
  "_extraction": {
    "status": "succeeded|failed",
    "run_id": "<agent_run_id>",
    "parsed_at": "<iso8601>",
    "prompt_version": "jd-paste-parsing-v1",
    "provider": "deepseek|fake",
    "model": "<model id>"
  },
    "fields": {
    "title": "...",
    "company": "...",
    "platform": "...",
    "location": "...",
    "salary_range": "...",
    "direction": "...",
    "responsibilities": ["..."],
    "hard_requirements": ["..."],
    "nice_to_have_requirements": ["..."],
    "benefits_or_risk_clues": ["..."],
    "uncertain_fields": [ { "field": "...", "reason": "..." } ]
  }
}
```

- The top-level `JobPosting` columns (`company`, `title`, `location`,
  `salary_range`, `direction`) hold the **user-edited final values**.
- `jd_normalized.fields` holds the **model-parsed draft** (including list
  fields like `responsibilities` that have no dedicated column).
- `jd_normalized._extraction` holds parse provenance (run_id, prompt_version,
  provider, model) for audit.
- When the user creates a job without parsing (manual entry),
  `jd_normalized` is null.

### Pydantic contract

- `backend/app/schemas/jd_paste_facts.py` (new):
  - `JdPasteFactsModelOutput` — the model-output contract validated as the
    persistence gate (mirrors `ResumeFactsModelOutput` at
    `schemas/resume_facts.py:66-85`). Every field optional to tolerate sparse
    JDs; `uncertain_fields` captures what needs user confirmation.
  - `UncertainField` — `{field: str, reason: str | None}` (mirrors
    `schemas/resume_facts.py:55-63`).
- `backend/app/schemas/jd_parse.py` (new):
  - `JdParseRequest` — `{raw_jd: str, platform: str | None = None}` with a
    validator that rejects whitespace-only text.
  - `JdParseRunSummary` — `{id: str, status: str, error: str | None}`.
  - `JdParseExtraction` — `{status, run_id, parsed_at, prompt_version,
    provider?, model?}` — parse provenance the frontend persists into
    `jd_normalized._extraction`.
  - `JdParseResponse` — `{status, run: JdParseRunSummary, fields:
    JdParseDraftFields, extraction: JdParseExtraction, raw_jd: str}`.

### Sanitization invariant

Step results and `AgentRun.result` contain **only**: counts (`raw_jd_len`,
`field_count`, `uncertain_count`), `truncation` metadata, `prompt_version`,
`message_count`, `provider`, `model`, `request_id`, `latency_ms`, `usage`,
validation `kind`. **Never** the raw JD text, full prompt, or parsed field
values (mirrors the invariant enforced by
`test_jd_analysis_api.py:260-285` and `test_resume_upload.py:274-289`).

## Frontend

### Paste-first create flow

Replace `JobCreateModal.tsx` with a two-phase paste-first flow (reference:
`ProfileDraftPanel` two-step confirm at `ResumeDetailPage.tsx:304-491` and
`reextractResumeFacts` loading pattern at `ResumeDetailPage.tsx:528-541`):

1. **Phase 1 — paste**: a `ProFormTextArea` for `jd_raw` with a "智能解析"
   button. On click: `setParsing(true)` → `await parseJobJd(rawJd)` →
   `setParsedFields(response.fields)` → switch to phase 2.
   - Parse failure: `message.warning("解析失败，请手动填写")`, show the failed
     run id/status with a link or button to `/agent-runs/{run_id}/detail`, keep
     fields empty, and still proceed to phase 2 (manual entry).
2. **Phase 2 — edit + save**: render the existing `ProFormText` fields
   (`company`, `title`, `location`, `salary_range`, `direction`) pre-filled
   from `parsedFields`, plus a read-only `ProFormTextArea` showing `jd_raw`.
   `company` and `title` stay `rules={[{ required: true }]}`.
   - On submit: `createJob({ ...editedFields, jd_raw, platform,
     jd_normalized: { _extraction: {...}, fields: parsedFields } })`.

### API client

- `frontend/src/api/client.ts`: add `parseJobJd(rawJd, platform?)` → `POST
  /api/v1/jobs/parse` (mirrors `reextractResumeFacts` at `client.ts:133-141`).
- `frontend/src/types/index.ts`: add `JdParseResponse`, `JdParseDraftFields`
  types. Extend `JobCreate` with optional `jd_normalized`. Extend `JobOut` with
  optional `jd_normalized`.

### Detail page

- `JobDetailPage.tsx`: extend the `Descriptions` block (L295-308) to show
  `jd_normalized.fields` metadata (responsibilities, requirements, etc.) when
  present. No editing in this task.

## Compatibility and Migration

- **No database migration required.** `jd_raw` (Text, `models.py:112`) and
  `jd_normalized` (JSON, `models.py:113`) already exist (migration
  `0001_initial.py:76-77`). `jd_normalized` is currently unused.
- `JobCreate.jd_normalized` and `JobOut.jd_normalized` are additive optional
  fields; existing API consumers that omit them are unaffected.
- Existing manually-created jobs have `jd_normalized = null`; the detail page
  degrades gracefully (hides the parsed-metadata section).
- Test database uses `Base.metadata.create_all` (`conftest.py:34-43`), so no
  migration is needed for tests.

## Trade-offs

- **Parse-then-create (not create-then-parse)**: chosen so the user can review
  parsed fields before committing a job. Cost: the `AgentRun` is not linked to
  a `job_id` (the job doesn't exist at parse time). The run's `result` carries
  enough provenance (field_count, prompt_version, provider, model) to audit
  without a job link.
- **HTTP 200 on model parse failure (not 502)**: parsing is an auxiliary
  preview step; the user can always fall back to manual entry. Returning 200
  with a failed run keeps the flow non-blocking. This differs from request
  validation (blank JD → 422) and from the resume re-extract endpoint (502)
  because there the extraction is the primary action.
- **No `job_repo` for parse**: the parse flow creates no `JobPosting`, so it
  only touches `agent_run_repo`. The light `job_repo.py` refactor is scoped to
  the existing create/list/get paths to align with Repository Rules without
  expanding scope.
- **Full parsed fields in API response, not in step metadata**: the parse
  response carries the full `fields` for the form to use; step results carry
  only counts. This keeps step metadata small and sanitized while delivering
  the draft to the frontend.

## Operational / Rollback

- Feature is additive: existing jobs and the manual create flow are
  unaffected. Rollback = remove the `POST /jobs/parse` route and revert
  `JobCreateModal.tsx`; existing `POST /jobs` continues to work with
  `jd_normalized` ignored.
- A failed parse leaves a durable failed `AgentRun` (visible immediately from
  the parse response and later through the agent-runs list filtered by
  `workflow_type="jd_paste_parsing"`). The user can still save a job with raw
  JD only (`jd_normalized = null`).
- No external side effects (no platform calls); fully idempotent and
  user-scoped.
