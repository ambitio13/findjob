# Design

## Backend

Change analysis submission:

1. Validate job belongs to current user.
2. Validate resume version belongs to current user and has usable raw text/facts.
3. Create `AgentRun(workflow_type="resume_aware_jd_analysis", status="queued", job_id=job_id)`.
4. Store sanitized run result metadata: `job_id`, `resume_version_id`, `resume_id`, `input_lengths`, no raw text.
5. Enqueue `JdAnalysisPayload`.
6. Return `RunJdAnalysisSubmitResponse`.

Worker handler:

1. Load run/job/resume/profile.
2. Mark run `running`.
3. Call a refactored `run_resume_aware_jd_analysis` using the existing run.
4. Persist `JobAnalysis` and `GeneratedArtifact` on success.
5. Mark failed on exceptions.

## Result Hydration

The existing job detail page already merges `listJobAnalyses(job)` with `listAgentRuns(job)`. Preserve that pattern:

- queued/running run with no analysis -> process panel only;
- succeeded run with analysis/artifact -> result panel;
- failed run with no analysis -> failure panel + trace.

## Duplicate Submit

Minimum MVP: disable submit while an active queued/running run exists for the same job/resume pair.

Better if cheap: backend idempotency key `jd_analysis:{user_id}:{job_id}:{resume_version_id}` while active.
