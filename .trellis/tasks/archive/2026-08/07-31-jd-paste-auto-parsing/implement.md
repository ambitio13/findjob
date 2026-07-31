# JD Paste Auto Parsing — Implementation Plan

## Execution Order

### Phase A — Backend parse pipeline (mirror resume_fact pattern)

1. **`backend/app/schemas/jd_paste_facts.py`** (new)
   - `UncertainField(BaseModel)` — `{field: str, reason: str | None = None}`.
   - `JdPasteFactsModelOutput(BaseModel)` — all fields optional / default-empty:
     `title`, `company`, `platform`, `location`, `salary_range`, `direction`,
     `responsibilities: list[str]`, `hard_requirements: list[str]`,
     `nice_to_have_requirements: list[str]`, `benefits_or_risk_clues: list[str]`,
     `uncertain_fields: list[UncertainField]`.
   - Reference: `schemas/resume_facts.py`.

2. **`backend/app/schemas/jd_parse.py`** (new)
   - `JdParseRequest(BaseModel)` — `{raw_jd: str, platform: str | None = None}`
     with a validator rejecting whitespace-only `raw_jd` (422, no run).
   - `JdParseRunSummary(BaseModel)` — `{id: str, status: str, error: str |
     None}` (from_attributes=True for `AgentRun`).
   - `JdParseResponse(BaseModel)` — `{status: Literal["succeeded", "failed"],
     run: JdParseRunSummary, fields: JdPasteFactsModelOutput, raw_jd: str}`.

3. **`backend/app/agents/prompts/jd_paste.py`** (new)
   - `PROMPT_VERSION = "jd-paste-parsing-v1"`.
   - `JD_RAW_CAP = 8_000`.
   - `_SYSTEM_PROMPT` — includes `_JD_PASTE_MARKER = "jd paste parsing
     assistant"` for fake routing; no-fabrication rules; output single JSON;
     no markdown; mark uncertain fields.
   - `_truncate(text, cap) -> tuple[str, dict]`.
   - `build_jd_parse_messages(raw_jd, platform_hint) -> JdPastePromptResult`
     (messages + truncation metadata).
   - Reference: `agents/prompts/resume_fact.py`.

4. **`backend/app/agents/jd_paste_executor.py`** (new)
   - `JdPasteValidationError(kind, message, *, request_id)` — mirrors
     `ResumeFactValidationError`.
   - `JdPasteExecution` dataclass — output + provider/model/request_id/
     latency_ms/usage.
   - `JdPasteExecutor(gateway: ModelGateway)`:
     - `build_prompt(raw_jd, platform_hint) -> JdPastePromptResult`.
     - `async call_model(messages) -> ChatResponse` (temperature=0.1).
     - `validate(response) -> JdPasteFactsModelOutput` — json.loads →
       model_validate, raise `JdPasteValidationError(kind="json"|"schema")`.
   - Reference: `agents/resume_fact_executor.py`.

5. **`backend/app/services/jd_parse_service.py`** (new)
   - `WORKFLOW_TYPE = "jd_paste_parsing"`.
   - Six step-name constants.
   - `JdParseOutcome` dataclass — `{status, run, fields, raw_jd}`.
   - `parse_jd(db, current_user, raw_jd, platform_hint, gateway) ->
     JdParseOutcome`:
     - Assumes API schema already rejected blank `raw_jd`; do not create a
       skipped-status branch for invalid input.
     - Create `AgentRun` (job_id=None).
     - Six steps with sanitized results (mirror
       `resume_fact_service.py:175-322`).
     - `_fail_run(...)` — failed step + failed run, commit, return failed
       outcome with `JdPasteFactsModelOutput()` empty fields (never raises for
       model/provider/schema failure).
   - Reference: `services/resume_fact_service.py`.

6. **`backend/app/models_gateway/fake.py`** (extend)
   - Add `_JD_PASTE_MARKER = "jd paste parsing assistant"`.
   - Add `_JD_PASTE_FAKE_OUTPUT` (deterministic schema-valid JSON with all
     fields populated).
   - Add routing branch in `chat()` for the marker.
   - Reference: `_RESUME_FACT_MARKER` / `_RESUME_FACT_FAKE_OUTPUT` in `fake.py`.

7. **`backend/app/api/v1/jobs.py`** (extend)
   - Add `POST /jobs/parse` route: inject `gateway`, call
     `jd_parse_service.parse_jd`, return `JdParseResponse`. Model/provider/
     schema failures return HTTP 200 with a failed run; blank request bodies
     are rejected by `JdParseRequest` with 422 before service entry.
   - Add `jd_normalized` to `create_job` constructor call.
   - Reference: `resumes.py:39-133` for gateway injection + inline service
     call.

8. **`backend/app/schemas/api.py`** (extend)
   - `JobCreate`: add `jd_normalized: dict[str, Any] | None = None`.
   - `JobOut`: add `jd_normalized: dict[str, Any] | None = None`.

9. **`backend/app/db/repositories/job_repo.py`** (new, light refactor)
   - `create(db, *, user_id, ...) -> JobPosting`.
   - `get_by_id(db, job_id, user_id) -> JobPosting | None`.
   - `list_for_user(db, user_id, ...) -> tuple[list[JobPosting], int]`.
   - Refactor `jobs.py` create/list/get to use `job_repo`.
   - Reference: `job_analysis_repo.py`, `resume_repo.py`.

### Phase B — Backend tests

10. **`backend/app/tests/test_jd_parse_contracts.py`** (new)
    - `JdPasteFactsModelOutput` validation: valid full, valid sparse (all
      optional), invalid (missing required `field` in `UncertainField`).
    - `build_jd_parse_messages`: marker present, schema inlined, truncation
      when JD > cap, platform_hint included.
    - `JdPasteExecutor.validate`: valid JSON → output; invalid JSON →
      `kind="json"`; schema mismatch → `kind="schema"`.

11. **`backend/app/tests/test_jd_parse_api.py`** (new)
    - Seed user via `_make_user` helper (mirror `test_jd_analysis_api.py`).
    - `test_parse_success`: fake gateway → 200, fields populated, run
      succeeded with 6 steps, step results sanitized (no raw JD).
    - `test_parse_partial`: fake output with some fields null → 200, run
      succeeded.
    - `test_parse_failure_invalid_json`: `_InvalidStubGateway` → 200, run
      failed, fields are the empty typed shape.
    - `test_parse_failure_raising`: `_RaisingStubGateway` → 200, run failed,
      fields are the empty typed shape.
    - `test_parse_blank_jd_validation`: blank/whitespace raw_jd → 422 and no
      `AgentRun` created.
    - `test_create_job_with_jd_normalized`: POST `/jobs` with
      `jd_normalized` → 201; GET `/jobs/{id}` returns it.
    - `test_create_job_without_jd_normalized`: POST `/jobs` without → 201,
      `jd_normalized` is null.
    - `test_parse_ownership`: cross-user parse is scoped (parse itself is
      user-scoped via `get_current_user`; no cross-user read needed since no
      job is created).
    - Sanitization assert: raw JD token (e.g. `SUPER_SECRET_JD_TOKEN_42`)
      must not appear in any `AgentStep.result` or `AgentRun.result`.

### Phase C — Frontend

12. **`frontend/src/types/index.ts`** (extend)
    - Add `JdParseDraftFields` interface.
    - Add `JdParseResponse` interface.
    - Extend `JobCreate` with `jd_normalized?: Record<string, unknown>`.
    - Extend `JobOut` with `jd_normalized?: Record<string, unknown> | null`.

13. **`frontend/src/api/client.ts`** (extend)
    - Add `parseJobJd(rawJd: string, platform?: string)` → `POST
      /api/v1/jobs/parse`, returns `JdParseResponse`.

14. **`frontend/src/features/jobs/JobCreateModal.tsx`** (rewrite)
    - Two-phase flow: paste → parse → edit → save.
    - Phase 1: `ProFormTextArea` for `jd_raw` + "智能解析" button with
      `parsing` loading state.
    - Phase 2: `ProFormText` fields pre-filled from parsed fields; `company`/
      `title` required; read-only `jd_raw` display.
    - Parse failure: `message.warning`, show failed run id/status (linking to
      AgentRun detail or otherwise making it inspectable), proceed to manual
      entry.
    - Submit: `createJob({ ...fields, jd_raw, platform, jd_normalized })`.

15. **`frontend/src/pages/jobs/JobDetailPage.tsx`** (extend)
    - Show `jd_normalized.fields` (responsibilities, hard_requirements, etc.)
      in a `Card type="inner"` when present.

## Validation Commands

Run per phase, full suite before task completion:

```bash
# Backend lint + format + tests
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q

# Frontend lint + type-check + build
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build

# Trellis task validation
python3 .trellis/scripts/task.py validate 07-31-jd-paste-auto-parsing
```

## Review Gates

- After Phase A (backend pipeline): run `pytest -q` for new contract tests
  before moving to API tests.
- After Phase B (backend tests): all backend tests green, sanitization
  asserts pass.
- After Phase C (frontend): `pnpm type-check` + `pnpm build` green.
- Final: full suite (backend + frontend) green before `task.py start` review.

## Risk Points

- **Sanitization**: step results must never contain raw JD text or full prompt.
  The sanitization assert test is mandatory.
- **Fake gateway routing**: the `_JD_PASTE_MARKER` must be unique and present
  in the system prompt for fake routing to work; otherwise tests fall through
  to the default fake response and fail schema validation.
- **`company`/`title` required**: the form must enforce required on these even
  after parse; the backend `JobPosting` constraint is the final gate.
- **No `job_id` on parse run**: `AgentRun.job_id` is null during parse. The
  parse response must surface the run id immediately, and the agent-runs list
  endpoint already supports filtering by `workflow_type`, so parse runs remain
  discoverable without a job link.
- **Repository refactor scope**: keep `job_repo.py` refactor minimal (create/
  get/list only) to avoid scope creep; do not refactor analysis endpoints.

## Rollback Points

- After Phase A: if tests fail, the parse pipeline is isolated — revert the
  new files and `jobs.py` route addition; existing create flow is unaffected.
- After Phase B: tests are additive; revert test files if needed.
- After Phase C: `JobCreateModal.tsx` rewrite is the riskiest frontend change;
  keep the old version in git history for quick revert. The backend API
  changes (`jd_normalized` optional field) are backward-compatible.
