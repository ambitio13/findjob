# JD Paste Auto Parsing

## Goal

Let users paste raw JD text and automatically parse structured job fields,
instead of manually filling a multi-field job form first. The parsed fields are
editable before the job is saved, preserving raw JD as the source of truth.

## Background

Manual JD entry is acceptable for the first internal version, but the desired UX
is paste-first: copy a JD from a platform, let the system parse company/title/
location/salary/direction/requirements, then let the user correct fields before
saving.

The project already has two structurally identical "raw text → ModelGateway
parse → structured fields → persist" workflows to mirror:

- `resume_fact_extraction` (resume upload auto-parsing) — the closest analog.
- `resume_aware_jd_analysis` (JD match analysis) — the original executor +
  service + 6-step run template.

Both persist an `AgentRun` with six ordered `AgentStep` records and route model
calls through `ModelGateway`. JD paste parsing will reuse the same architecture.

## Decisions

- **Parse-then-create flow**: the user pastes raw JD, clicks "parse", receives
  structured draft fields, edits them, then saves to create the `JobPosting`.
  Parsing happens before the job exists; the job is created only on save.
- **AgentRun observability**: every parse is recorded as an `AgentRun`
  (`workflow_type = "jd_paste_parsing"`) with six ordered steps, mirroring the
  resume-fact and JD-analysis pattern. Because the job does not exist yet,
  `AgentRun.job_id` is null at parse time; after the job is created the run is
  not retroactively linked (the run result already carries enough provenance).
- **company/title stay required**: the `JobPosting` non-null constraints on
  `company` and `title` are preserved. The parse result auto-fills these fields
  in the edit form; if the model could not extract them the user must fill them
  manually before save. No schema migration is needed.
- **Create-only scope**: this task delivers the paste → parse → edit → create
  flow only. A future task will add `PATCH /jobs/{id}` and detail-page editing.
- **Storage**: raw JD text goes to the existing `JobPosting.jd_raw` column; the
  parsed structured result goes to the existing `jd_normalized` JSON column
  (currently unused). No migration required.
- **Model access**: all model calls go through `ModelGateway` with
  `temperature = 0.1`, matching the other parse workflows.
- **Failure handling**: parse failures are visible and recoverable. A failed
  parse still returns a response with a failed `AgentRun` and empty structured
  fields so the user can fill fields manually and save the job with raw JD only.
  The save endpoint does not require `jd_normalized`.
- **Input validation**: blank JD text is a request validation error (`422`) and
  does not create an `AgentRun`. Only model/provider/schema failures after a
  valid paste are treated as recoverable parse failures (`200` with a failed
  run).

## Requirements

### Parse endpoint

- `POST /api/v1/jobs/parse` accepts raw JD text (and an optional platform
  hint) for the current user. `raw_jd` must contain non-whitespace text.
- Calls `ModelGateway` to parse the raw JD into structured fields:
  - `title`
  - `company`
  - `platform` / source when inferable or manually selectable
  - `location`
  - `salary_range`
  - `direction`
  - `responsibilities`
  - `hard_requirements`
  - `nice_to_have_requirements`
  - `benefits_or_risk_clues` when present
  - `uncertain_fields` listing fields the model could not confidently extract
- Records an `AgentRun` (`workflow_type = "jd_paste_parsing"`) with six ordered
  steps: `load_context`, `build_prompt_context`, `call_model`,
  `validate_model_output`, `persist_outputs` (run metadata only — no job yet),
  `complete_run`.
- Returns the parsed draft fields plus the `AgentRun` summary (id, status,
  error when failed). Step metadata is sanitized: no raw prompt or full JD text
  in step results, only lengths / counts / provider / model / prompt version /
  validation status.
- On model failure or validation failure after valid input, persists a failed
  `AgentRun` + failed step and returns a response with empty typed draft fields
  and the failed run, so the user can still proceed to manual entry. HTTP status
  is 200 (recoverable parse), not 502, because the parse is an auxiliary preview
  step, not the primary create action.
- Preserves raw JD text as the source of truth (returned unchanged in the
  response for the form to re-submit on save).

### Create endpoint adjustment

- The existing `POST /api/v1/jobs` create endpoint accepts an optional
  `jd_normalized` field. When provided, it is persisted to
  `JobPosting.jd_normalized`. When omitted, `jd_normalized` stays null.
- `JobOut` exposes `jd_normalized` so the detail page can display parsed
  metadata.
- Ownership stays scoped through `get_current_user` and user-filtered queries.

### Frontend

- Replace (or augment) the current `JobCreateModal` manual form with a
  paste-first flow:
  1. A `ProFormTextArea` for raw JD text with a "智能解析" button.
  2. On parse, show loading state, then render editable `ProFormText` /
     `ProFormTextArea` fields pre-filled with parsed draft values.
  3. `company` and `title` remain required in the form; if the parse did not
     fill them the user must fill them before save.
  4. On save, POST to `/api/v1/jobs` with `jd_raw`, the edited top-level fields,
     and `jd_normalized` (the parse result).
- Parse failures show a non-blocking warning (e.g. `message.warning`), display
  the failed run id/status with a way to inspect the run detail, and leave the
  fields editable so the user can still save manually.
- The raw JD text is always retained in the form and submitted as `jd_raw`.

### Tests

- Successful parse: fake gateway returns schema-valid JSON; endpoint returns
  draft fields and a succeeded run with six steps.
- Partial parse: some fields missing → returned as null / empty, run still
  succeeded.
- Failure fallback: stub gateway returns invalid JSON (or raises) → run failed,
  response has empty typed draft fields, HTTP 200, user can still save.
- Blank input: whitespace-only `raw_jd` returns 422 and creates no run.
- Create with `jd_normalized`: POST `/jobs` persists both `jd_raw` and
  `jd_normalized`; GET `/jobs/{id}` returns them.
- Ownership: parse and create are scoped to `get_current_user`; cross-user
  access returns 404.
- No raw JD text or full prompt is persisted in any `AgentStep.result`.
- Fake gateway routing: a dedicated `_JD_PASTE_MARKER` in the system prompt
  routes the fake provider to return a schema-valid JD parse output.

## Acceptance Criteria

- [x] User can paste raw JD and click parse to receive structured draft fields.
- [x] User can edit parsed fields (including filling required company/title)
      before saving.
- [x] Saved jobs retain raw JD in `jd_raw` and parsed metadata in
      `jd_normalized`.
- [x] Parse failures do not block manual save; the failed run id/status is
      shown in the parse response/UI and is inspectable through AgentRun detail.
- [x] Every valid parse attempt creates an auditable `AgentRun` with six
      ordered steps containing only sanitized metadata.
- [x] Tests cover successful parse, partial parse, failure fallback, blank
      input validation, create with `jd_normalized`, and user ownership.

## Notes

- This is P2 behind observability and resume/profile corrections.
- The parse endpoint does not create a `JobPosting`; it only returns a draft.
  The job is created by the existing `POST /jobs` endpoint (lightly extended).
- A future task will add `PATCH /jobs/{id}` and detail-page editing / re-parse.
