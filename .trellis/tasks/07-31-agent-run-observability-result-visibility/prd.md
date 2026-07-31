# Agent Run Observability and Result Visibility

## Goal

Make agent execution auditable in development and ensure completed analysis
results are visible in the frontend even when the page did not retain the
original POST response.

This is P0 because the current product can run a JD analysis, persist data in
the database, and still leave the user with no visible result or intermediate
process.

## Confirmed Evidence

- `frontend/src/pages/jobs/JobDetailPage.tsx` stores the analysis response only
  in local `result` state after `runJdAnalysis`; it does not reliably hydrate
  the latest persisted analysis on page load.
- `frontend/src/api/client.ts` already exposes `listJobAnalyses`, but the job
  detail page does not use it.
- `backend/app/services/jd_analysis_service.py` persists `AgentRun`,
  `AgentStep`, `JobAnalysis`, and `GeneratedArtifact` rows during the model-backed
  JD analysis flow.
- `backend/app/api/v1/agent_runs.py` lists and reads user-scoped agent runs, but
  the frontend does not display detailed run steps for the JD analysis workflow.

## Requirements

- R1. Job detail must load persisted analyses for the current job and display
  the latest completed result when one exists.
- R2. After clicking "run JD analysis", the frontend must not depend only on the
  immediate POST response. It must refresh from persisted backend state.
- R3. The user must be able to see an ordered run process in development,
  including at least:
  - create run / queued or started;
  - load profile;
  - load resume version;
  - load JD;
  - build context;
  - call model;
  - validate model output;
  - persist analysis;
  - generate artifact;
  - succeeded or failed.
- R4. Failed runs must show a readable, sanitized failure reason and the step
  where the failure occurred.
- R5. Run detail/step APIs must remain scoped to `current_user.id`. A user must
  not be able to inspect another user's runs, steps, analyses, or artifacts.
- R6. Model prompts, raw resume text, API keys, and oversized raw model payloads
  must not be exposed in the frontend audit view. Show summaries, IDs, status,
  timestamps, provider/model metadata, and validation metadata.
- R7. The UI may be verbose in development. Clarity and debuggability are more
  important than minimizing noise.

## Acceptance Criteria

- [ ] Reloading a job detail page with existing `JobAnalysis` rows displays the
  latest persisted analysis result.
- [ ] Running a new JD analysis refreshes the persisted analysis list and shows
  the newly completed result.
- [ ] The UI shows ordered agent steps with status, timing, and sanitized
  metadata/error details.
- [ ] If the model call or validation fails, the UI shows a failed run and does
  not silently leave the user at an empty state.
- [ ] Backend tests cover successful result readback, failed run visibility, and
  user-scoped access to run steps/details.
- [ ] Frontend tests or type/build checks cover the new result hydration and run
  process rendering contracts.
- [ ] Full quality gate passes: backend lint/format/tests, frontend lint/type/build.

## Notes

- This task should not introduce platform automation or automatic application
  submission.
