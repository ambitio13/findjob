# Resume Upload Auto Profile Parsing — Design

## Context

The current resume upload flow (`backend/app/api/v1/resumes.py:34-114`) extracts
raw text for `.txt`/`.pdf`/`.docx` via `backend/app/services/resume_parser.py`
and stores only parser telemetry in `ResumeVersion.parsed_facts` (the `_parser`
and `_parser_status` keys, `resume_parser.py:43-53`). There is no structured
candidate memory, no profile draft path, and JD analysis only ever sees raw text
plus the telemetry dict (`jd_analysis_service.py:113-124`, prompt
`agents/prompts/jd_analysis.py:120-128`).

This task adds an auditable, model-backed structured-facts extraction step that
fires after text extraction, stores typed resume facts plus a reviewable profile
draft inside `parsed_facts`, never silently overwrites the live `UserProfile`,
and feeds structured facts into JD analysis.

## Architecture and Boundaries

### New component: resume fact extraction

A new executor + service pair mirroring the JD-analysis pattern
(`agents/jd_analysis_executor.py` + `services/jd_analysis_service.py:232-504`):

- `backend/app/agents/resume_fact_executor.py`
  - `ResumeFactExecutor(gateway: ModelGateway)` — injected, no direct provider
    calls (PRD requirement; mirrors `jd_analysis_executor.py:81-82`).
  - `build_prompt(raw_text, filename) -> list[ChatMessage]`.
  - `call_model(messages) -> ChatResponse` via `gateway.chat` (temperature low).
  - `validate(response) -> ResumeFactsModelOutput` — `json.loads` then
    `ResumeFactsModelOutput.model_validate`, raising
    `ResumeFactValidationError(kind="json"|"schema")` (mirrors
    `jd_analysis_executor.py:114-151`).

- `backend/app/agents/prompts/resume_fact.py`
  - `build_resume_fact_messages(raw_text, filename)` — system prompt (extraction
    assistant, no fabrication, mark uncertain fields), user message (schema +
    capped raw text, cap constant mirroring `RESUME_RAW_TEXT_CAP` at
    `agents/prompts/jd_analysis.py:24`).

- `backend/app/services/resume_fact_service.py`
  - `WORKFLOW_TYPE = "resume_fact_extraction"`.
  - `extract_resume_facts(resume, version, gateway, db) -> AgentRun`
    orchestration mirroring `run_resume_aware_jd_analysis`
    (`jd_analysis_service.py:232-504`):
    1. `load_context` — verify ownership, confirm `raw_text` non-empty
       (422 on blank, mirroring `jd_analysis_service.py:164-171`).
    2. `build_prompt_context` — sanitized step result (filename, text length,
       cap/truncation metadata); never raw text.
    3. `call_model` — sanitized (provider/model/request_id/latency/usage).
    4. `validate_model_output` — sanitized (validation status).
    5. `persist_outputs` — write typed `facts` + `_extraction` status into
       `version.parsed_facts`; commit.
    6. `complete_run`.
  - `_fail_run(...)` helper mirroring `jd_analysis_service.py:277-307`:
    persist failed step + failed run (sanitized), commit. The service returns a
    failed extraction status to upload callers and raises HTTP 502 only for the
    explicit re-extract endpoint.

### Trigger points

- **Upload path**: extend `POST /api/v1/resumes` (`resumes.py:34-114`) to inject
  `gateway: ModelGateway = Depends(get_model_gateway_dep)`. After
  `resume_repo.create_version(...)` and `db.commit()`, call
  `resume_fact_service.extract_resume_facts(...)` inline. There is no background
  worker and no "fake async" completion in v1: when the upload response returns,
  extraction has either succeeded or failed and the `parsed_facts._extraction`
  block plus `AgentRun` detail are already readable.
  - Upload success is defined by file storage + raw-text parsing. If extraction
    fails after the resume/version row exists, `POST /resumes` still returns
    `201` with `_extraction.status="failed"` and `run_id`; the user should see
    the failed extraction trail and can retry via the re-extract endpoint.
    Upload should return 502 only when file/raw-text parsing itself fails before
    the resume version is usable.

- **Re-extract endpoint**: `POST /api/v1/resumes/{resume_id}/versions/{version_id}/extract`
  creates a fresh `AgentRun` and re-runs extraction on existing `raw_text`,
  refreshing `parsed_facts.facts`. Idempotent on the same version (overwrites
  the prior `facts` object). User-scoped (404 on cross-user, mirroring
  `_require_owned_run` at `agent_runs.py:49-54`). Unlike upload, this explicit
  extraction action may return 502 on model/provider/schema failure after the
  failed `AgentRun` has been persisted.

### Observability

Reuse `AgentRun` + `AgentStep` (`models.py:182-217`) and
`agent_run_repo` (`db/repositories/agent_run_repo.py`) with
`workflow_type="resume_fact_extraction"`. The run is not tied to a `job_id`
(`AgentRun.job_id` stays null; it's nullable per migration 0002). Step results
are sanitized (counts, IDs, provider/model/prompt_version, latency, validation
status) — never raw resume text (matches the no-logging invariant enforced by
`test_resume_upload.py:274-289` and `test_jd_analysis_api.py:260-285`).

Failed extraction persists a failed run + failed step (sanitized error). Upload
callers receive the saved resume plus failed extraction status; explicit
re-extract callers receive 502 because extraction is the primary action.

## Data Flow and Contracts

### `parsed_facts` shape (the durable contract)

`ResumeVersion.parsed_facts` (JSON, `models.py:85`) keeps telemetry under
underscore keys and gains a `facts` object plus an `_extraction` status block:

```jsonc
{
  "_parser": "pdfplumber",            // existing telemetry
  "_parser_status": "parsed",         // existing telemetry
  "_extraction": {
    "status": "succeeded|failed|needs_confirmation|not_run",
    "run_id": "<agent_run_id>",
    "extracted_at": "<iso8601>",
    "prompt_version": "<semver>",
    "provider": "deepseek|fake",
    "model": "<model id>"
  },
  "facts": {                          // typed, validated object
    "contact": { "name": "...", "email": "...", "phone": "..." },
    "education": [ { "school": "...", "degree": "...", "major": "...", "period": "..." } ],
    "work_experience": [ { "company": "...", "title": "...", "period": "...", "summary": "..." } ],
    "projects": [ { "name": "...", "role": "...", "summary": "..." } ],
    "skills": [ "..." ],
    "years_of_experience": 5,
    "target_direction": "...",
    "locations": [ "..." ],
    "strengths": [ "..." ],
    "highlights": [ "..." ],
    "uncertain_fields": [ { "field": "...", "reason": "..." } ]
  }
}
```

All `facts` sub-fields are nullable / default-empty; sparse resumes still
produce a valid object with `uncertain_fields` populated. Unsupported formats
(status `unsupported`) leave `facts` absent and `_extraction.status = "not_run"`.

### Pydantic contract

- `backend/app/schemas/resume_facts.py` (new):
  - `ResumeFactsModelOutput` — the model-output contract validated as the
    persistence gate (mirrors `JdAnalysisModelOutput` at
    `schemas/jd_analysis.py:59-81`). Every field optional to tolerate sparse
    resumes; `uncertain_fields` captures what needs user confirmation.
- The existing `ResumeVersionOut.parsed_facts: dict[str, Any] | None`
  (`schemas/resume.py:24`) stays untyped at the transport boundary (it already
  carries arbitrary JSON), but downstream readers (`_resume_to_dict`, the
  profile draft UI) read the typed `facts` key.

### Profile draft

The extracted profile-shaped fields live inside `parsed_facts.facts` under a
reserved sub-namespace. Concretely, the draft the user reviews is derived from
`facts` (target_direction, locations, strengths, years_of_experience, etc.) and
is surfaced as read-only draft fields in the resume detail UI. The user applies
a draft via an explicit merge action (see API below) — extraction never writes
to `UserProfile` directly (satisfies PRD "do not silently overwrite",
`prd.md:33-34`).

The live `UserProfile` (`models.py:39-54`, `schemas/user.py`) remains the only
source of confirmed truth. Field-shape coordination with the sibling task
`07-31-structured-profile-text-fields` happens through this `facts` draft: the
sibling task's explicit text fields will be populated from a confirmed draft.

### Profile draft handoff

This task does **not** write resume facts into `UserProfile`. It exposes a
reviewable draft inside `parsed_facts.facts` and renders that draft in the
resume detail UI. The sibling `07-31-structured-profile-text-fields` task owns
the explicit profile form and the apply/merge action. Clear draft mappings are:

- `target_direction` → `career_direction`
- `locations[0]` → `base_location`
- `locations` → `preferred_locations`
- `strengths` / `highlights` → `strengths`
- `years_of_experience`, `uncertain_fields`, and constraints-like notes →
  named text fields defined by the profile task, not raw JSON editing.

### JD analysis consumption

Update so JD analysis reasons over structured facts, not just telemetry:

- `_resume_to_dict` (`jd_analysis_service.py:113-124`): expose
  `facts = version.parsed_facts.get("facts") or {}` as a clean `facts` key,
  keep `parser_status`/`parser_name`, keep `raw_text`.
- Prompt builder `build_jd_analysis_messages`
  (`agents/prompts/jd_analysis.py:86-197`): in the `## RESUME` section, render
  the typed `facts` (contact, education, work_experience, skills,
  years_of_experience, target_direction, strengths, highlights,
  uncertain_fields) as structured context, and update the instructions to tell
  the model to reason over these facts in addition to `raw_text`.
- `JdAnalysisModelOutput` (`schemas/jd_analysis.py:59-81`): no structural
  change required for this AC (output shape unchanged), but prompt-version bump
  recorded in provenance.

## Compatibility and Migration

- No database migration required. `parsed_facts` is already `JSON nullable`
  (`models.py:85`, migration `0001_initial.py:52-62`). New keys are additive.
- Existing rows with only `_parser`/`_parser_status` remain valid:
  `facts`/`_extraction` are simply absent, treated as `not_run`.
- `_resume_to_dict` and the JD-analysis prompt must degrade gracefully when
  `facts` is absent (existing tests
  `test_load_context_handles_none_parsed_facts` and the
  `{"skills":["Go"]}` case at `test_jd_analysis_contracts.py:558-646` already
  assert degradation; update them to cover the new shape).
- FakeModelGateway (`models_gateway/fake.py`) needs a new route for the
  extraction prompt marker (mirroring the `_JD_ANALYSIS_MARKER` pattern at
  `fake.py:28`) so offline/test extraction returns a deterministic
  `ResumeFactsModelOutput`.

## Trade-offs

- **Inline extraction during upload**: avoids building a background worker for
  v1 (matches JD analysis today) at the cost of the upload request bearing model
  latency. This is explicit, not fake async. Acceptable for development
  observability per the parent PRD's "willing to tolerate detailed UI noise"
  decision.
- **Draft-in-parsed_facts vs. new column/table**: chosen to avoid schema
  migration and keep one source of resume truth; cost is that the draft is
  JSON-typed at the boundary and the apply endpoint does the mapping.
- **Typed `facts` object vs. flat keys**: chosen for a versioned, validatable
  contract; cost is a new Pydantic model and slightly more nesting.

## Operational / Rollback

- Feature is additive on existing rows; rollback = stop calling extraction and
  leave `facts` absent. Existing upload + JD-analysis paths continue to work
  (they already tolerate `parsed_facts=None`/`{}`).
- A failed extraction leaves `parsed_facts._extraction.status = "failed"` and a
  durable failed `AgentRun`; the resume + raw text remain usable and upload
  still returns the saved resume. Re-extract endpoint allows retry without
  re-upload and may return 502 for extraction-specific failures.
- No external side effects (no platform calls); fully idempotent and
  user-scoped.
