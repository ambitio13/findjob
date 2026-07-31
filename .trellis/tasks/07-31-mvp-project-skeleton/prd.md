# MVP Project Skeleton

## Goal

Build the first runnable MVP skeleton for the job-search agent product. The
skeleton must establish backend, frontend, data-service, model-gateway, and
lightweight agent-runtime foundations so later tasks can implement resume
parsing, JD analysis, generated snippets, HR opening messages, and skill-gap
plans without reworking the architecture.

## Background

Project direction is defined in `项目立项/` and `.trellis/spec/`:

- Product: an AI agent for job seekers.
- MVP scope: manual JD entry/import, resume upload/parse foundation, JD
  analysis foundation, generated resume rewrite snippets, HR opening messages,
  and skill-gap/interview-preparation output.
- Out of MVP scope: job-platform automation, automatic resume submission,
  automatic HR messaging, browser automation, and complete Word/PDF customized
  resume export.
- Agent direction: PiAgent-style layered runtime with planner, executor,
  reflector, tool registry, memory, audit/logging, and model gateway.
- Model direction: OpenAI-compatible provider abstraction, initially configured
  for DeepSeek.

## Requirements

### R1. Repository Structure

Create a clear full-stack source layout:

- `backend/` for FastAPI backend.
- `frontend/` for React + Ant Design Pro frontend.
- `docker-compose.yml` for local PostgreSQL, Redis, backend, and frontend.
- Documentation for local startup and environment variables.

### R2. Backend Foundation

Create a runnable FastAPI application with:

- health endpoint;
- settings/config module;
- structured logging foundation;
- database session module;
- Redis client module;
- versioned API router;
- placeholder routers for users, resumes, jobs, applications, and agent runs;
- test setup and at least one health/API smoke test.

### R3. Data Model Foundation

Create initial persistence models or migration-ready schemas for:

- user profile and job-search preferences;
- resume and resume version;
- manually entered JD/job posting;
- JD analysis;
- generated artifact;
- application record;
- agent run;
- agent step;
- tool call.

The implementation may use SQLAlchemy/Alembic or another explicit Python ORM and
migration setup, but it must be documented and consistent.

### R4. Model Gateway

Create a backend model gateway interface so product code never calls provider
SDKs directly.

The initial provider should support OpenAI-compatible chat calls configured for
DeepSeek through environment variables. If no API key is present, local tests
must still pass by using a fake or disabled provider path.

### R5. Lightweight Agent Runtime Foundation

Create the first internal runtime interfaces/classes for:

- `AgentRun`;
- `AgentStep`;
- `ToolCall`;
- `Artifact`;
- planner;
- executor;
- reflector;
- tool registry;
- memory abstraction.

This task only needs foundation and a minimal smoke flow. It does not need a
fully capable reasoning agent.

### R6. Frontend Foundation

Create a runnable React + Ant Design Pro frontend with:

- application shell/navigation;
- dashboard or jobs page;
- manual JD entry screen or modal;
- jobs table placeholder;
- job detail placeholder;
- agent run status placeholder;
- generated artifact placeholder;
- API health/status integration.

### R7. Developer Experience

Provide commands for:

- local Docker startup;
- backend tests;
- frontend lint/type/build checks;
- full-stack smoke check where practical.

## Acceptance Criteria

- [ ] A fresh developer can start the stack from documented commands.
- [ ] Backend health endpoint returns success.
- [ ] Frontend renders the app shell and can display backend health/status.
- [ ] PostgreSQL and Redis are available through Docker Compose.
- [ ] Backend contains typed modules for config, database, Redis, model gateway,
  and agent runtime foundation.
- [ ] DeepSeek is only accessed through the model gateway abstraction.
- [ ] Missing model API keys do not break local tests.
- [ ] Data model foundation covers user profile, resume version, job posting,
  JD analysis, generated artifact, application record, agent run, agent step,
  and tool call.
- [ ] MVP scope excludes platform automation and complete resume export.
- [ ] Tests or smoke checks prove backend and frontend start successfully.

## Review Correction

This task should not be treated as completion of the usable business MVP.
It is a project skeleton task only.

Current implementation status after review:

- Agent runtime exists structurally, but the JD-analysis flow is still a smoke
  workflow. Even when a real API key is configured, the demo flow does not yet
  call the model to produce real analysis.
- Model Gateway exists and can select DeepSeek when configured, but product
  Agent flows have not been wired to model-backed planning, analysis, or
  artifact generation.
- User profile and role/preference APIs are placeholders.
- Resume upload, storage, parsing, versioning API, and frontend upload workflow
  are not implemented.
- Generated resume rewrite snippets, HR opening messages, and skill-gap plans
  are placeholders or future artifact types, not functional product flows.

Do not archive this task as "business MVP complete". It may be accepted only as
"runnable skeleton complete" after checks pass. The usable MVP requires follow-up
tasks for model-backed JD analysis, user profile, resume upload/parsing, and
artifact generation.

## Out of Scope

- Automated job-platform crawling or login.
- Automatic resume submission.
- Automatic HR messaging.
- Browser automation.
- Full customized Word/PDF resume export.
- Production RAG/vector database implementation.
- Complex multi-agent planning.
- Payment, admin console, or deployment pipeline.

## Handoff Prompt for Coding Agents

```text
Active task: .trellis/tasks/07-31-mvp-project-skeleton

Read AGENTS.md, .trellis/workflow.md, this task's prd.md/design.md/implement.md,
and all files listed in implement.jsonl before coding. Implement only the MVP
project skeleton described here. Do not add platform automation or complete
resume export.
```
