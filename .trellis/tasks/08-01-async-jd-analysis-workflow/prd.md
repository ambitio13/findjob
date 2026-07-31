# Async JD Analysis Workflow

## Goal

Move resume-aware JD analysis to queued execution so clicking "run analysis" returns immediately, the job detail page shows progress, and final analysis/artifacts hydrate when ready.

## Current Evidence

- `frontend/src/pages/jobs/JobDetailPage.tsx` awaits `runJdAnalysis(...)`.
- `backend/app/api/v1/jobs.py` awaits `run_resume_aware_jd_analysis(...)`.
- Failed runs are now visible through `AgentRun.job_id` and `GET /agent-runs?job_id=...`.

## Requirements

- Starting analysis creates a queued `AgentRun` linked to `job_id`, `resume_version_id`, and current user.
- API returns immediately with the run id.
- Worker performs the existing six-step JD analysis workflow.
- Success creates `JobAnalysis` and `GeneratedArtifact` exactly once for the run.
- Failure leaves a failed `AgentRun` visible under the job without creating analysis/artifact rows.
- Job detail page shows queued/running steps and hydrates result after success.
- Duplicate clicks must not accidentally create many identical active runs.

## Acceptance Criteria

- [ ] `POST /api/v1/jobs/{job_id}/analyses` no longer awaits model analysis.
- [ ] Submit response includes queued `agent_run`.
- [ ] `GET /agent-runs?job_id=...` shows queued/running/failed/succeeded runs.
- [ ] Worker success persists analysis/artifact and links both to `agent_run_id`.
- [ ] Worker failure persists failed run/step and no analysis/artifact.
- [ ] Frontend can select a running or failed run and see process state.
- [ ] Tests cover enqueue, worker success, worker failure, duplicate submit behavior, cross-user job/resume scoping, and no raw content leakage.

## Dependencies

- Depends on `08-01-queue-runtime-foundation`.
- Should run after `08-01-async-resume-fact-extraction` if analysis requires freshly extracted resume facts in normal UX.
