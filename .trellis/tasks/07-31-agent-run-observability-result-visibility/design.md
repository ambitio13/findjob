# Agent Run Observability and Result Visibility Design

## Current Problem

The JD analysis workflow persists data, but the frontend treats the POST response
as the main source of truth. If the page reloads, the POST response is gone. If a
run fails, the user sees an error toast but not the durable run trail. That makes
the agent feel like a black box and blocks engineering review.

## Backend Design

Keep the workflow synchronous for now, but make persisted state first-class.

### Read APIs

Use existing route families where possible:

- `GET /api/v1/jobs/{job_id}/analyses`
  Returns current user's persisted analyses for the job. Extend the response if
  needed so the frontend can reconstruct the same `AnalysisResult` view from
  persisted rows, artifact metadata, and run metadata.

- `GET /api/v1/agent-runs/{run_id}`
  Returns a user-scoped run. Extend or complement it with ordered steps.

- Candidate addition:
  `GET /api/v1/agent-runs/{run_id}/steps`
  Returns ordered user-scoped steps with sanitized metadata.

Do not expose raw prompt text, raw resume text, API keys, or full model payloads.

### Step Persistence

The service should persist meaningful steps around the existing fixed workflow:

1. `load_context`
2. `build_prompt_context`
3. `call_model`
4. `validate_model_output`
5. `persist_outputs`
6. `complete_run`

If existing implementation already has coarser steps, it may either refine those
steps or add metadata to the current step structure. The important contract is
that the UI can show enough detail for engineering audit.

### Failure Handling

On model/provider/schema failures:

- persist `AgentRun.status = failed`;
- persist the failing step with a sanitized error type/message;
- do not create `JobAnalysis` or `GeneratedArtifact` unless output validation
  succeeded;
- return an API error that includes `run_id` when a failed run was persisted, so
  the frontend can link to the failure trail.

## Frontend Design

`JobDetailPage` should hydrate from backend state:

- load job detail;
- load resume options;
- load existing job analyses via `listJobAnalyses`;
- select/show the latest persisted analysis if present;
- after `runJdAnalysis`, refresh analyses and show the new persisted result;
- show an agent process panel for the selected analysis's `agent_run_id`.

The process panel can be verbose:

- step name;
- status tag;
- started/completed timestamps or duration when available;
- sanitized metadata;
- error details for failed steps.

## Data Ownership

Every read path must scope through the current user:

- job must belong to the current user;
- run must belong to the current user;
- analyses must belong to a job owned by the current user;
- artifacts shown through an analysis/run must belong to the same source IDs or
  be reachable only through a user-scoped job/run path.

## Compatibility

Existing rows may have only one or a few steps. The UI should degrade gracefully
instead of assuming all new step names exist.

## Non-Goals

- Streaming tokens.
- Full asynchronous job queue.
- Displaying hidden chain-of-thought.
- Browser/job-platform automation.
