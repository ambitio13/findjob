# Readiness Artifact Generation

## Goal

Generate application-ready materials for one application record while preserving
provenance, retryability, and old successful artifacts after failures.

## Required Artifacts

- `hr_opening_message`
- `resume_rewrite_snippet`
- `skill_gap_plan`
- `interview_prep`

## Requirements

- Each artifact generation is an async AgentRun-backed workflow.
- Each workflow validates model output before writing `GeneratedArtifact`.
- Every artifact stores `user_id`, `job_id`, `resume_version_id`,
  `agent_run_id`, `prompt_version`, `model_name`, and `source_ids`.
- Generated artifacts attach to an application record timeline.
- Existing successful artifacts remain visible after failed retries.
- Source changes mark artifacts stale instead of overwriting or hiding them.
- Duplicate active generation for the same application/artifact/source hash is
  blocked or attached to existing active run.

## Failure Handling Requirements

- Queue enqueue failure creates a failed run/failure envelope.
- Model call failure is retryable.
- Invalid JSON/schema output is failed and not persisted as artifact.
- Missing job/resume/profile context is non-retryable until source is fixed.
- Stale source after run start should prevent "current" marking.

## Acceptance Criteria

- [ ] User can generate all four artifact types for an application record.
- [ ] Each successful artifact is linked to sources and AgentRun provenance.
- [ ] Failed generation is visible and retryable when appropriate.
- [ ] A failed retry does not remove the previous successful artifact.
- [ ] Source freshness/staleness is test-covered.
- [ ] No raw resume/JD text is stored in AgentRun step metadata.

