# 7.31 First Version Product Fixes Design

## Architecture Direction

This parent task coordinates a sequence of independently verifiable fixes. The
first coding priority is not more model capability; it is observability and
result retrieval, because every later automated parse/generation flow will need
the same audit surface.

The version correction keeps the current architecture:

- FastAPI route handlers validate transport and delegate to services.
- Domain services own workflow decisions and persistence.
- Model access stays behind `ModelGateway`.
- PostgreSQL remains the source of truth for users, resumes, jobs, agent runs,
  steps, analyses, and artifacts.
- React + Ant Design Pro remains the operational frontend.

## Dependency Flow

```text
Archive predecessor tasks
        |
        v
Agent run observability + result visibility
        |
        v
Resume upload auto profile parsing
        |
        v
Structured profile text fields
        |
        v
JD paste auto parsing
```

The P0 observability task is intentionally before resume/JD parsing. Once run
steps and result loading are dependable, the same pattern can explain resume
profile extraction and JD parsing workflows.

## Shared Contracts

Agent-facing workflows should persist and expose:

- run id, workflow type, status, start/end timestamps, user id;
- ordered step records with names, status, timing, metadata, and sanitized error;
- source IDs for job, resume, resume version, profile, artifact, and analysis;
- model metadata such as provider, model name, prompt version, and validation
  status;
- final output references rather than large raw prompt bodies.

Profile-facing workflows should move from amorphous JSON constraints toward a
named set of editable text fields. Existing columns may be reused where
compatible, but the frontend must not ask users to write JSON.

## Rollout Notes

- Prefer synchronous run execution plus polling/readback for the first
  correction. Streaming can be a later optimization.
- Preserve existing endpoints where possible and add read endpoints for run
  detail / steps / latest analysis as needed.
- Keep old database rows readable; migrations should be additive unless a
  specific child task proves a schema replacement is unavoidable.

## Review Gates

Each child task must define its own acceptance criteria and validation commands.
The parent is complete only when all children are either archived as completed or
explicitly deferred with an updated reason.
