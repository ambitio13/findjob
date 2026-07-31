# MVP Project Skeleton Design

## Architecture

```text
frontend/ React + Ant Design Pro
  |
  | HTTP JSON API
  v
backend/ FastAPI
  |
  +-- PostgreSQL: durable product and agent state
  +-- Redis: cache, locks, queues, hot context
  +-- Model Gateway: OpenAI-compatible provider abstraction
  +-- Agent Runtime: planner/executor/reflector/tools/memory
```

## Backend Boundaries

Suggested source layout:

```text
backend/
  app/
    main.py
    api/
      deps.py
      v1/
        health.py
        users.py
        resumes.py
        jobs.py
        applications.py
        agent_runs.py
    core/
      config.py
      logging.py
      security.py
    db/
      session.py
      models/
      repositories/
      migrations/
    cache/
      redis.py
    models_gateway/
      base.py
      deepseek.py
      fake.py
    schemas/
    services/
    agents/
      runtime.py
      planner.py
      executor.py
      reflector.py
      memory.py
      tools/
        registry.py
```

FastAPI routers validate transport concerns and delegate to services. Services
own business state transitions. Agent runtime code must not be embedded inside
route handlers.

## Frontend Boundaries

Suggested source layout:

```text
frontend/
  src/
    app/
    pages/
      dashboard/
      jobs/
    features/
      jobs/
      resumes/
      applications/
      agent-runs/
    components/
      layout/
      common/
    api/
      client.ts
      types/
```

Use Ant Design Pro for the operational app shell, job table, forms, detail
views, status components, and placeholders.

## Data Entities

Initial entities should be migration-ready even if some fields are minimal:

- `UserProfile`: career direction, base/location preference, salary range,
  strengths, constraints.
- `Resume`: uploaded file metadata.
- `ResumeVersion`: parsed facts and version metadata.
- `JobPosting`: manually entered JD plus normalized company/title/location.
- `JobAnalysis`: match score, risk score, salary/growth/stability analysis,
  summary.
- `GeneratedArtifact`: artifact type, source IDs, prompt/model metadata, content.
- `ApplicationRecord`: job, resume version, status, timeline.
- `AgentRun`: workflow type, status, user, timestamps.
- `AgentStep`: planned step status and result.
- `ToolCall`: tool name, schema version, input/output metadata, status.

## Model Gateway Contract

Business code depends on an interface similar to:

```python
class ModelGateway:
    async def chat(self, request: ChatRequest) -> ChatResponse:
        ...

    async def structured(self, request: StructuredRequest[T]) -> T:
        ...
```

Configuration:

- `MODEL_PROVIDER`
- `MODEL_BASE_URL`
- `MODEL_API_KEY`
- `MODEL_DEFAULT_MODEL`

The DeepSeek provider uses an OpenAI-compatible API shape. A fake provider must
exist for tests and no-key local development.

## Minimal Agent Runtime Contract

The first runtime should support a smoke workflow such as:

```text
manual_jd_analysis_demo
  -> create AgentRun
  -> create AgentStep("plan")
  -> create AgentStep("generate_placeholder_analysis")
  -> save GeneratedArtifact
  -> mark AgentRun succeeded
```

This can be deterministic or fake-model backed. The goal is structural
foundation, not high-quality AI output yet.

## API Shape

Minimum endpoints:

- `GET /api/v1/health`
- `GET /api/v1/agent-runs/{id}`
- `POST /api/v1/jobs` for manual JD creation
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{id}`

Optional if cheap:

- `POST /api/v1/agent-runs/manual-jd-analysis-demo`

All list endpoints should be pagination-ready.

## Operational Notes

- Docker Compose should include PostgreSQL and Redis.
- Missing model credentials must not prevent local tests.
- Secrets must come from environment variables, not committed files.
- Keep generated artifacts granular; complete resume export is deferred.

## Risks

- Overbuilding the agent runtime before the product workflow exists.
- Pulling platform automation into the first skeleton.
- Letting frontend types drift from backend contracts.
- Calling DeepSeek directly from arbitrary modules.

