# Backend Directory Structure

The backend source tree does not exist yet. Use this structure when creating
the FastAPI application unless later source code establishes a better local
pattern.

```text
backend/
  app/
    main.py
    api/
      deps.py
      v1/
        users.py
        resumes.py
        jobs.py
        applications.py
        agents.py
    core/
      config.py
      security.py
      logging.py
      idempotency.py
    db/
      session.py
      migrations/
      models/
      repositories/
    schemas/
      user.py
      resume.py
      job.py
      application.py
      agent.py
    services/
      profile_service.py
      resume_service.py
      job_search_service.py
      job_analysis_service.py
      application_service.py
      interview_prep_service.py
    agents/
      planner.py
      executor.py
      reflector.py
      memory.py
      prompts/
      tools/
    integrations/
      job_platforms/
      llm_providers/
    workers/
    tests/
```

## Placement Rules

- Put FastAPI routers under `app/api/v1/`.
- Put request/response schemas under `app/schemas/`.
- Put durable database models and repositories under `app/db/`.
- Put product decisions under `app/services/`.
- Put model planning, tool execution, memory, prompts, and reflection under
  `app/agents/`.
- Put external job-board, resume-parser, LLM-provider, and browser-automation
  adapters under `app/integrations/`.
- Put background job entrypoints under `app/workers/`.

Avoid placing platform-specific scraping logic, prompt construction, or database
writes directly in route handlers.

