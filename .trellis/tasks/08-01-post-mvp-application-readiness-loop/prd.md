# Post-MVP Application Readiness Loop

## Goal

Turn the current usable MVP into a failure-aware application-readiness loop:
for each job, the system should help the user prepare auditable application
materials, track application state, recover from failed AI/background work, and
make an explicit user-approved next action.

The goal is not to rush into platform automation. The goal is to build the
state, audit, retry, and approval foundation that an automated application
agent must rely on.

## Current Facts

- The project already supports resume upload, text parsing, async structured
  resume-fact extraction, profile draft application, JD paste parsing, and
  resume-aware JD analysis.
- Model-backed workflows already use Redis/arq workers, durable `AgentRun`
  rows, ordered `AgentStep` rows, and sanitized metadata.
- `GeneratedArtifact` already has planned artifact types for `jd_analysis`,
  `hr_opening_message`, `resume_rewrite_snippet`, `skill_gap_plan`, and
  `interview_prep`.
- `ApplicationRecord` exists in the database model but the API route is still a
  placeholder.
- External side effects such as submitting resumes or sending HR messages are
  intentionally out of the MVP and require explicit approval, idempotency, and
  audit logs before implementation.

## Product Requirements

### R1. Application Readiness Package

For a job + selected resume version, the system should produce a "readiness
package" made of granular artifacts:

- HR opening message;
- resume rewrite snippets for projects / skills / experience;
- skill-gap plan;
- interview preparation plan;
- references to the JD analysis and source resume version used.

Each generated artifact must preserve source IDs, prompt version, model
metadata, and agent run provenance.

### R2. Application Record State Machine

The placeholder applications surface must become a durable application record
center. Each record should bind:

- `job_id`;
- `resume_version_id`;
- generated artifact IDs used for this application;
- current status;
- timeline events;
- latest failure / blocked reason when applicable.

Recommended first statuses:

- `planned` — user intends to apply;
- `preparing` — materials are being generated or refreshed;
- `materials_ready` — required artifacts exist and are current;
- `approval_required` — external action is possible but waiting for user
  confirmation;
- `approved` — user approved the next external action;
- `submitted` — user or agent completed submission;
- `failed` — latest preparation/action failed but may be retried;
- `paused` — user intentionally stopped progress;
- `rejected` — application was rejected;
- `interviewing` — interview process started.

### R3. Failure-First UX

Every application readiness workflow must show:

- what operation is running or failed;
- what source data it used;
- whether the failure is retryable;
- what the user can do next;
- where the audit trail lives.

No indefinite spinners. No "maybe saved" ambiguity. No hidden model failure.

### R4. Retry and Idempotency

Retry must be deliberate and scoped:

- retrying material generation creates or reuses a clear `AgentRun`;
- successful historical artifacts remain visible;
- stale artifacts are marked stale, not silently overwritten;
- duplicate clicks do not create duplicate active work for the same job/resume
  operation;
- external actions must have idempotency keys before platform automation begins.

### R5. Human Approval Boundary

Any action that changes an external system must be separated from internal
preparation:

- internal preparation: generate artifacts, update application record, analyze
  job;
- approval boundary: show exact planned platform action and payload;
- external execution: only after explicit user approval.

The first automated application agent must be semi-automatic: it may prepare and
fill, but it should stop before final submission unless the user has approved
that exact action.

### R6. Automatic Application Agent Readiness Gate

The project is ready to start a first automated application agent only when:

- application records have a reliable state machine and timeline;
- readiness packages can be generated and regenerated with provenance;
- failed internal workflows have clear retry/recovery behavior;
- user approval can be represented durably;
- every future external action has a planned idempotency key and audit event;
- one target platform has been selected for a limited pilot;
- platform failures such as login expiry, CAPTCHA, selector drift, rate limit,
  network failure, duplicate submission, and partial submission have explicit
  handling rules.

## Failure Categories To Design For

### Internal AI / Queue Failures

- queue enqueue failure;
- worker crash or timeout;
- model call failure;
- invalid model JSON;
- schema validation failure;
- stale source data after generation starts;
- duplicate active runs;
- missing or deleted source resume/job.

### Data Consistency Failures

- job exists but belongs to another user;
- resume version has no parsed text;
- readiness artifacts reference an outdated resume version;
- application record status conflicts with active work;
- timeline event write succeeds but artifact write fails, or vice versa.

### User-Control Failures

- user edits JD/profile/resume after materials were generated;
- user pauses an in-flight application;
- user approves an action, then source data changes before execution;
- user attempts to submit without required artifacts.

### Future Platform Automation Failures

- login/session expired;
- CAPTCHA or anti-bot challenge;
- platform page layout changed;
- required form field is missing or unexpected;
- file upload fails;
- platform returns rate limit;
- network/browser process fails mid-action;
- duplicate application detected;
- final submit result is unknown.

## Out Of Scope For This Planning Task

- Implementing automatic platform submission.
- Implementing browser automation.
- Sending HR messages automatically.
- Building full Word/PDF resume export.
- Optimizing generated prompt quality beyond contract requirements.

## Acceptance Criteria

- [ ] A full PRD/design/implementation plan exists for the application-readiness
  loop.
- [ ] The plan prioritizes failure states, retries, idempotency, audit, and
  user approval before external automation.
- [ ] The plan decomposes the work into independently verifiable child tasks.
- [ ] The plan defines a readiness gate for starting the first automated
  application agent.
- [ ] The first automated agent is explicitly scoped as semi-automatic and
  user-approved, not autonomous bulk submission.
- [ ] No implementation is started from this planning task without a separate
  user go-ahead.

