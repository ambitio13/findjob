# Resume-Aware JD Analysis Agent

## Goal

Replace the deterministic smoke JD-analysis flow with a real resume-aware,
model-backed Agent workflow that uses the existing Model Gateway. The Agent must
analyze a JD against the current user's profile and selected resume version.

## Background

The skeleton task created planner/executor/reflector/runtime types and a
`manual_jd_analysis_demo` smoke workflow. That is not a real Agent: the executor
does not use the configured model and the artifact is placeholder text.

This task depends on user context/profile and resume upload/parsing foundation.
JD-only analysis is not enough for the product; the useful workflow is
JD + user profile + resume facts.

## Requirements

- Define structured output schemas for JD analysis:
  - role summary;
  - responsibilities;
  - hard requirements;
  - nice-to-have requirements;
  - risk points;
  - salary/growth/stability notes;
  - match score and risk score when resume facts are available;
  - skill-gap and interview-preparation suggestions.
- Accept `job_id` and `resume_version_id` as workflow inputs.
- Load current user profile, selected resume version facts, and JD before model
  execution.
- Reject analysis when the job or resume version does not belong to the current
  user.
- Make Agent execution call `ModelGateway.structured` or `ModelGateway.chat`
  through an executor/service, never a provider SDK directly.
- Persist generated outputs as `JobAnalysis` and `GeneratedArtifact` records.
- Store provider, model, request ID, prompt version, and source IDs with
  artifacts where available.
- Expose an endpoint to run JD analysis for an existing job ID.
- Frontend job detail page can trigger analysis and display structured result.
- Missing API keys select fake provider and keep tests deterministic.

## Acceptance Criteria

- [ ] With `MODEL_PROVIDER=deepseek` and `MODEL_API_KEY` present, JD analysis
  flow calls the model through Model Gateway.
- [ ] Analysis request requires an existing job and selected resume version.
- [ ] Analysis uses user profile and resume facts in addition to JD text.
- [ ] With no API key, tests use fake provider and do not make network calls.
- [ ] Route handlers do not import DeepSeek/OpenAI/httpx provider code directly.
- [ ] Analysis output is structured and validated before persistence.
- [ ] `JobAnalysis` and `GeneratedArtifact` records are created for a run.
- [ ] Frontend displays analysis status and generated analysis content on the job
  detail page.
- [ ] Tests cover fake-provider analysis, validation failure, and persistence.

## Out of Scope

- Platform crawling or automatic job discovery.
- Resume file export.
- Full RAG/vector search.
- Automatic HR messaging or resume submission.
