# Resume-Aware JD Analysis Agent — Implementation Plan

Implement in phases. Stop after each phase and run the listed verification
before moving on. This task is intentionally staged because it touches backend
contracts, model execution, durable state, and frontend UI.

## Phase 0 — Planning Gate

- [ ] Confirm `design.md` is reviewed.
- [ ] Confirm task dependencies are completed:
      - `07-31-user-profile-preferences`;
      - `07-31-resume-upload-parsing-foundation`.
- [ ] Run:
      `python3 .trellis/scripts/task.py validate 07-31-model-backed-jd-analysis-agent`.
- [ ] Start task via Trellis only after approval.

**Phase 0验收**

- `design.md` and `implement.md` exist.
- Trellis manifests are valid.
- No code changes required in this phase.

## Phase 1 — Backend Schemas & API Contract

Deliverable: typed contracts compile, but no model call yet.

- [ ] Create `backend/app/schemas/jd_analysis.py`:
      - `JdAnalysisRiskPoint`;
      - `JdAnalysisEvidence`;
      - `JdAnalysisModelOutput`;
      - `RunJdAnalysisRequest`;
      - `JobAnalysisOut`;
      - `GeneratedArtifactOut`;
      - `RunJdAnalysisResponse`;
      - `JobAnalysisListOut`.
- [ ] Score fields use `Field(ge=0, le=100)`.
- [ ] Add frontend mirrored types later; keep backend first.
- [ ] Extend `backend/app/api/v1/jobs.py` with route signatures only if useful,
      but do not wire model behavior until Phase 4.

**Phase 1 验收**

```bash
cd backend
.venv/bin/ruff check app
.venv/bin/ruff format --check app
.venv/bin/python - <<'PY'
from app.schemas.jd_analysis import JdAnalysisModelOutput
print(JdAnalysisModelOutput.model_json_schema()["title"])
PY
```

Review checklist:

- Schema can represent every PRD output field.
- No provider import appears outside `app/models_gateway`.

## Phase 2 — Repositories & Context Loader

Deliverable: load all source data and enforce ownership, without calling model.

- [ ] Create `backend/app/db/repositories/job_analysis_repo.py`:
      - `create_analysis`;
      - `list_for_job`;
      - `get_for_job` if needed.
- [ ] Create `backend/app/db/repositories/generated_artifact_repo.py`:
      - `create_artifact`;
      - optional `get`.
- [ ] Create `backend/app/db/repositories/agent_run_repo.py` or small helpers:
      - create/update `AgentRun`;
      - create `AgentStep`;
      - list/get user-scoped runs where touched.
- [ ] Create `backend/app/services/jd_analysis_service.py` with:
      - `load_jd_analysis_context(db, current_user, job_id, resume_version_id)`.
- [ ] Ownership rules:
      - job missing or not owned by current user → 404 `job not found`;
      - resume version missing or parent resume not owned by current user → 404
        `resume version not found`;
      - resume version raw_text empty/blank → 422
        `resume version has no parsed text`.
- [ ] Add focused backend tests for ownership/context loading.

**Phase 2 验收**

```bash
cd backend
.venv/bin/ruff check app
.venv/bin/pytest -q app/tests/test_jd_analysis_agent.py
```

Review checklist:

- No route handler owns complex queries.
- Cross-user resources return 404, not 403.
- Resume version belongs to a resume owned by the current user.

## Phase 3 — Prompt & Model-Backed Executor

Deliverable: deterministic structured analysis can be produced through
`ModelGateway`, with fake provider working offline.

- [ ] Create `backend/app/agents/prompts/jd_analysis.py`:
      - `PROMPT_VERSION = "jd-analysis-v1"`;
      - `build_jd_analysis_messages(context) -> list[ChatMessage]`;
      - clear no-invention rule for resume facts.
- [ ] Add `JdAnalysisExecutor` in `backend/app/agents/executor.py` or a focused
      module if the file grows too large.
- [ ] Executor behavior:
      - calls `ModelGateway.chat`;
      - parses `ChatResponse.content` as JSON;
      - validates `JdAnalysisModelOutput`;
      - returns validated output plus provider/model/request/usage metadata.
- [ ] Update `FakeModelGateway.chat` to return schema-valid JSON when the prompt
      is for `resume_aware_jd_analysis`, while preserving existing fake behavior
      for smoke tests.
- [ ] Add invalid-output test using a fake gateway stub that returns malformed
      JSON or schema-invalid JSON.

**Phase 3 验收**

```bash
cd backend
.venv/bin/ruff check app
.venv/bin/pytest -q app/tests/test_jd_analysis_agent.py app/tests/test_model_gateway.py
rg -n "httpx|DeepSeekModelGateway|openai|deepseek" app/agents app/services app/api/v1 \
  | rg -v "models_gateway|MODEL_PROVIDER" && exit 1 || true
```

Review checklist:

- Business code depends on `ModelGateway`, not provider SDK/provider classes.
- Output is Pydantic-validated before any DB write that claims success.
- Prompt separates facts from instructions.

## Phase 4 — Orchestration, Persistence & API

Deliverable: backend endpoint creates durable run, steps, analysis, and artifact.

- [ ] Add `run_resume_aware_jd_analysis(...)` orchestration function:
      - fixed steps: `load_context`, `analyze_with_model`, `persist_outputs`;
      - creates/updates `AgentRun`;
      - creates `AgentStep` rows.
- [ ] Implement `POST /api/v1/jobs/{job_id}/analyses` in `jobs.py`:
      - body: `RunJdAnalysisRequest`;
      - depends on `get_current_user`, `get_db_session`, `get_model_gateway_dep`;
      - delegates to `jd_analysis_service`.
- [ ] Implement `GET /api/v1/jobs/{job_id}/analyses`:
      - user-scoped job check;
      - paginated list.
- [ ] Persist `JobAnalysis` mapping:
      - match/risk scores;
      - salary/growth/stability JSON notes;
      - summary.
- [ ] Persist `GeneratedArtifact`:
      - `artifact_type="jd_analysis"`;
      - `content` as validated output JSON string;
      - `source_ids` includes user/job/resume/version/provider/request/usage;
      - `prompt_version`;
      - `model_name`.
- [ ] User-scope `/agent-runs` list/get if touched by frontend display.

**Phase 4 验收**

```bash
cd backend
.venv/bin/ruff check app
.venv/bin/ruff format --check app
.venv/bin/pytest -q
```

Review checklist:

- Successful run creates `AgentRun`, `AgentStep`, `JobAnalysis`,
  `GeneratedArtifact`.
- Failed model validation persists failed run/step and returns 502.
- Artifact source metadata contains `job_id` and `resume_version_id`.
- No raw full prompt or full resume content is logged.

## Phase 5 — Frontend Integration

Deliverable: job detail page can run and display real resume-aware analysis.

- [ ] Extend `frontend/src/types/index.ts`:
      - JD analysis request/response;
      - structured output types;
      - analysis list types if used.
- [ ] Extend `frontend/src/api/client.ts`:
      - `runJdAnalysis(jobId, resumeVersionId)`;
      - `listJobAnalyses(jobId, page, pageSize)`.
- [ ] Replace the demo analysis card in
      `frontend/src/pages/jobs/JobDetailPage.tsx` with a real panel.
- [ ] UI behavior:
      - load resumes;
      - choose resume and version;
      - handle no-resume state with link/copy pointing user to `/resumes`;
      - run analysis;
      - show run status, scores, summary, requirements, risks, gaps, prep list,
        source metadata.
- [ ] Keep generated analysis visually separate from future external actions.

**Phase 5 验收**

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

Review checklist:

- Button no longer calls `manual-jd-analysis-demo`.
- User cannot run analysis without selecting a resume version.
- No UI claims the output is a completed customized resume.

## Phase 6 — Integration & Docker Gate

Deliverable: full project passes local validation.

- [ ] Backend:

```bash
cd backend
.venv/bin/ruff check app
.venv/bin/ruff format --check app
.venv/bin/pytest -q
```

- [ ] Frontend:

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

- [ ] Trellis:

```bash
python3 .trellis/scripts/task.py validate 07-31-model-backed-jd-analysis-agent
```

- [ ] Scope/provider guard:

```bash
rg -n "auto_submit|selenium|playwright|完整简历导出|自动发送HR|browser_automation" backend/app frontend/src || true
rg -n "httpx|DeepSeekModelGateway|OpenAI|openai" backend/app/agents backend/app/services backend/app/api/v1 \
  | rg -v "models_gateway|MODEL_PROVIDER" && exit 1 || true
```

- [ ] Docker:

```bash
docker compose build
```

**Phase 6 验收**

- All checks pass.
- Vite chunk-size warning is acceptable; build errors are not.
- Task status can be marked completed only after this gate.

## Suggested Commit Slices

Use staged commits so review stays readable:

1. `feat: add jd analysis backend contracts`
   - schemas, prompt, repositories, context loader tests.
2. `feat: run resume aware jd analysis agent`
   - executor/orchestrator/API/persistence tests.
3. `feat: show jd analysis in job detail`
   - frontend API/types/UI.

If one coding agent does the whole task, it should still pause for review after
Phase 2 and Phase 4 before moving on.

## Rollback Points

- After Phase 2: remove new service/repositories/tests; no API behavior changed.
- After Phase 4: revert backend analysis endpoint and persistence helpers; no
  migration to roll back.
- After Phase 5: revert frontend job detail integration; backend can remain
  usable through API.

## Done Definition

- Phase 1-6 checklists are complete.
- Real analysis path uses `job_id + resume_version_id + current_user`.
- Model calls go only through `ModelGateway`.
- Structured output is validated before persistence.
- `JobAnalysis`, `GeneratedArtifact`, `AgentRun`, and `AgentStep` are durable.
- Frontend job detail can run analysis and display structured output.
- No out-of-scope automation/export/RAG work is added.
