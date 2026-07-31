# Resume-Aware JD Analysis Agent — Design

## 1. Goal & Scope

Replace the smoke `manual_jd_analysis_demo` with a real, resume-aware,
model-backed JD analysis workflow. The workflow must analyze one existing job
against the current user's profile and one selected resume version.

This task is still MVP-scoped: it is a deterministic orchestration flow with a
single structured model call, not a free-form autonomous tool loop.

### In Scope

- `POST /api/v1/jobs/{job_id}/analyses` with body `{resume_version_id}`.
- `GET /api/v1/jobs/{job_id}/analyses` to list prior analyses for the current
  user's job.
- Load and validate ownership for:
  - current `UserProfile`;
  - `JobPosting`;
  - selected `ResumeVersion` and its parent `Resume`.
- Build an explicit agent context from profile + JD + resume version.
- Call the model through `ModelGateway` only.
- Validate model output with Pydantic before persistence.
- Persist:
  - `AgentRun`;
  - `AgentStep` rows for the staged workflow;
  - `JobAnalysis`;
  - `GeneratedArtifact` with source IDs, prompt version, provider/model/request
    metadata where available.
- Frontend job detail page:
  - lets user select a resume version;
  - runs analysis;
  - displays structured analysis and run status.
- Fake provider remains deterministic for tests and no-key local development.

### Out of Scope

- Free-form ReAct loop / model-selected arbitrary tools.
- LangGraph or another durable workflow engine.
- Background queue, websocket/SSE streaming, cancellation, or approval pause.
- Platform crawling, browser automation, auto apply, HR messaging.
- Complete resume export or generated Word/PDF resume.
- Full RAG/vector search.
- Structured resume fact extraction beyond task2 parser metadata/raw text.

## 2. Current State

Existing skeleton:

```text
AgentOrchestrator
  -> StaticPlanner
  -> PlaceholderExecutor
  -> Reflector
  -> InMemoryAgentMemory
  -> ToolRegistry skeleton
  -> ModelGateway abstraction
```

Current limitation:

- `manual_jd_analysis_demo` only accepts raw JD text.
- `PlaceholderExecutor` does not use the model for business output.
- The output is placeholder text, not a validated JD analysis.
- `AgentRun` and `GeneratedArtifact` are persisted, but not user/profile/resume
  aware.

Task3 target:

```text
API request
  -> ownership + context loading
  -> deterministic agent run
  -> structured model call through ModelGateway
  -> Pydantic validation
  -> JobAnalysis + GeneratedArtifact persistence
  -> frontend display
```

## 3. Architecture Decision

Use a small internal PiAgent-style layered runtime. For this task, the model
does not decide which tools to call. The app does the deterministic reads and
passes a verified context to the model.

| Layer | Responsibility in Task3 |
| --- | --- |
| API route | Validate transport/body, resolve current user, call service. No provider imports. |
| Service | Own workflow transaction, ownership checks, persistence mapping. |
| Context loader | Read profile/job/resume version, build `JdAnalysisContext`. |
| Planner | Produce fixed steps: `load_context`, `analyze_with_model`, `persist_outputs`. |
| Executor | Build messages, call `ModelGateway.chat`, parse JSON, validate output. |
| Reflector | MVP structural gate: fail if step failed or validated output missing. |
| Model Gateway | Provider boundary; DeepSeek uses OpenAI-compatible API, fake is deterministic. |
| Repositories | Encapsulate queries/writes for analysis/artifact/run state. |
| Frontend | Select resume version, trigger run, display structured result. |

Why not model tool-calling yet:

- The needed "tools" are predictable read/write operations.
- Letting the model decide reads adds risk without product value for this step.
- The runtime still leaves room to introduce real tool calls later.

## 4. Backend Files

```text
backend/app/
  agents/
    planner.py                    # + resume_aware_jd_analysis fixed plan
    executor.py                   # + JdAnalysisExecutor
    orchestrator.py               # + run_resume_aware_jd_analysis
    prompts/
      jd_analysis.py              # NEW: prompt version + message builder
  api/v1/
    jobs.py                       # + POST/GET analyses endpoints
    agent_runs.py                 # user-scope list/get for existing routes
  db/repositories/
    job_analysis_repo.py          # NEW
    generated_artifact_repo.py    # NEW
    agent_run_repo.py             # NEW or small persistence helpers
  schemas/
    jd_analysis.py                # NEW structured output + API schemas
  services/
    jd_analysis_service.py        # NEW workflow orchestration service
  tests/
    test_jd_analysis_agent.py     # NEW
```

No DB migration is required for MVP. Existing `JobAnalysis`,
`GeneratedArtifact`, `AgentRun`, and `AgentStep` columns are sufficient.
`JobAnalysis` does not have `resume_version_id`; source provenance is stored in
`GeneratedArtifact.source_ids` and `AgentRun.result`.

## 5. API Contract

### 5.1 Run Analysis

```http
POST /api/v1/jobs/{job_id}/analyses
Content-Type: application/json

{
  "resume_version_id": "ver_xxx"
}
```

Response `201`:

```json
{
  "agent_run": {
    "id": "run_xxx",
    "workflow_type": "resume_aware_jd_analysis",
    "status": "succeeded",
    "started_at": "...",
    "finished_at": "...",
    "error": null,
    "result": {
      "job_id": "job_xxx",
      "resume_version_id": "ver_xxx",
      "analysis_id": "analysis_xxx",
      "artifact_id": "art_xxx"
    }
  },
  "analysis": {
    "id": "analysis_xxx",
    "job_id": "job_xxx",
    "agent_run_id": "run_xxx",
    "match_score": 78,
    "risk_score": 34,
    "summary": "...",
    "salary_analysis": {"note": "..."},
    "growth_analysis": {"note": "..."},
    "stability_analysis": {"note": "..."}
  },
  "artifact": {
    "id": "art_xxx",
    "artifact_type": "jd_analysis",
    "prompt_version": "jd-analysis-v1",
    "model_name": "deepseek-chat",
    "content": "{validated structured JSON as text}",
    "source_ids": {
      "user_id": "demo_user",
      "job_id": "job_xxx",
      "resume_version_id": "ver_xxx",
      "resume_id": "res_xxx",
      "model_request_id": "req_xxx",
      "provider": "deepseek"
    }
  },
  "structured": {
    "role_summary": "...",
    "responsibilities": [],
    "hard_requirements": [],
    "nice_to_have_requirements": [],
    "risk_points": [],
    "match_score": 78,
    "risk_score": 34,
    "skill_gaps": [],
    "interview_preparation": [],
    "salary_note": "...",
    "growth_note": "...",
    "stability_note": "..."
  }
}
```

Errors:

| Case | Status | Detail |
| --- | --- | --- |
| Job missing or cross-user | 404 | `job not found` |
| Resume version missing or cross-user | 404 | `resume version not found` |
| Resume version has no raw text | 422 | `resume version has no parsed text` |
| Model output is not valid JSON/schema | 502 | `model returned invalid analysis` |
| Model provider/network failure | 502 | `model analysis failed` |

### 5.2 List Analyses For Job

```http
GET /api/v1/jobs/{job_id}/analyses?page=1&page_size=20
```

Returns current user's analyses for that job, newest first. The MVP list can
return `JobAnalysisOut` rows plus minimal artifact metadata; full run detail can
still be read from `/agent-runs/{run_id}`.

## 6. Structured Output Contract

`schemas/jd_analysis.py`:

```python
class JdAnalysisRiskPoint(BaseModel):
    title: str
    detail: str
    severity: Literal["low", "medium", "high"]


class JdAnalysisEvidence(BaseModel):
    claim: str
    source: Literal["jd", "resume", "profile"]
    quote: str | None = None


class JdAnalysisModelOutput(BaseModel):
    role_summary: str
    responsibilities: list[str]
    hard_requirements: list[str]
    nice_to_have_requirements: list[str]
    resume_match_evidence: list[JdAnalysisEvidence]
    risk_points: list[JdAnalysisRiskPoint]
    salary_note: str
    growth_note: str
    stability_note: str
    match_score: int | None = Field(default=None, ge=0, le=100)
    risk_score: int | None = Field(default=None, ge=0, le=100)
    skill_gaps: list[str]
    interview_preparation: list[str]
    recommendation: Literal["strong_match", "possible_match", "weak_match", "not_enough_info"]
```

Rules:

- If resume raw text is present, `match_score` and `risk_score` should be set.
- The prompt must tell the model not to invent resume facts.
- Evidence quotes must come from JD, resume text, or profile fields. If unsure,
  use no quote and explain uncertainty in `risk_points`.
- Pydantic validation is the persistence gate.

## 7. Agent Context

`JdAnalysisContext` should include:

```python
user_id: str
profile: UserProfileRead
job: JobOut or internal compact dict
resume:
  resume_id: str
  resume_version_id: str
  filename: str
  parser_status: str | None
  raw_text: str
  parsed_facts: dict[str, Any] | None
```

Context size guard:

- Use `resume_version.raw_text` but cap prompt inclusion to a conservative
  configured length (for MVP: e.g. 12k chars).
- Use full `jd_raw` unless it exceeds a configured cap (for MVP: e.g. 8k chars).
- Store truncation metadata in `AgentRun.result.source_context`.

## 8. Prompt Contract

Prompt module:

```python
PROMPT_VERSION = "jd-analysis-v1"

def build_jd_analysis_messages(context: JdAnalysisContext) -> list[ChatMessage]:
    ...
```

Message structure:

- system: role, output JSON requirements, no invention rule.
- user: separated sections:
  - user profile/preferences;
  - resume text and parser metadata;
  - JD text;
  - requested output schema.

Model call:

- Use `ModelGateway.chat` for Task3 so the response retains provider/model/
  request ID/usage metadata via `ChatResponse`.
- Parse `ChatResponse.content` as JSON and validate into
  `JdAnalysisModelOutput`.
- Do not import `httpx`, DeepSeek, OpenAI, or provider code outside
  `models_gateway`.

## 9. Persistence Mapping

Within one service-owned transaction:

1. Create `AgentRun(status="running", user_id=current_user.id,
   workflow_type="resume_aware_jd_analysis")`.
2. Create step `load_context` succeeded.
3. Create step `analyze_with_model` succeeded or failed.
4. On validated output:
   - create `JobAnalysis`;
   - create `GeneratedArtifact`;
   - update `AgentRun(status="succeeded", result={source IDs + output IDs})`.
5. On failure:
   - update `AgentRun(status="failed", error=...)`;
   - persist failed step with sanitized error;
   - raise the appropriate API error.

`JobAnalysis` mapping:

- `match_score` ← output.match_score
- `risk_score` ← output.risk_score
- `summary` ← output.role_summary plus concise recommendation summary
- `salary_analysis` ← `{"note": output.salary_note}`
- `growth_analysis` ← `{"note": output.growth_note}`
- `stability_analysis` ← `{"note": output.stability_note}`

`GeneratedArtifact` mapping:

- `artifact_type = "jd_analysis"`
- `content = output.model_dump_json()`
- `source_ids` includes user/job/resume/version/model request/provider/usage.
- `prompt_version = PROMPT_VERSION`
- `model_name = chat_response.model`

## 10. Frontend Design

Job detail page replaces the demo card with a real "JD 分析" panel:

- Load job detail as today.
- Load current user's resumes via `GET /resumes`.
- Let user choose one resume summary, then load its versions via
  `GET /resumes/{resume_id}/versions`.
- Default to first resume's latest version when available.
- Button: `运行 JD 分析`.
- POST `/jobs/{job_id}/analyses`.
- Display:
  - run ID + status;
  - match/risk scores;
  - role summary;
  - responsibilities;
  - hard/nice-to-have requirements;
  - risk points;
  - skill gaps;
  - interview preparation;
  - source metadata (job ID, resume version ID, prompt version, model).

Empty/error states:

- No resume uploaded: show a prompt to upload from `/resumes`.
- Selected resume version has unsupported/no raw text: show backend 422 detail.
- Model failure: show safe retry message; do not lose the selected resume.

## 11. Testing Strategy

Backend:

- Fake-provider successful analysis creates `AgentRun`, `AgentStep`,
  `JobAnalysis`, and `GeneratedArtifact`.
- Request rejects missing/cross-user job.
- Request rejects missing/cross-user resume version.
- Request rejects resume version with empty raw text.
- Route handlers do not import provider modules directly.
- Invalid model JSON/schema maps to 502 and persists failed run/step.
- `GET /jobs/{job_id}/analyses` is user-scoped.

Frontend:

- `pnpm lint`, `pnpm type-check`, `pnpm build`.
- Manual/browser smoke optional for this task: job detail can choose a resume
  version, trigger analysis, and display structured output.

## 12. Phased Delivery Gates

This task should be implemented and reviewed in stages:

1. Backend contracts only.
2. Backend context loading + ownership.
3. Model-backed executor + fake deterministic output.
4. Persistence + API integration.
5. Frontend trigger/display.
6. Final integration and Docker build.

Each phase has a separate verification gate in `implement.md`.
