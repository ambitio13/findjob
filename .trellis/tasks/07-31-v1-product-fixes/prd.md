# 7.31 First Version Product Fixes

## Goal

Turn the July 31 first-version review into a focused correction program that
makes the product usable as an engineering-grade job-search agent: prior
planning artifacts are closed cleanly, agent execution becomes observable and
auditable, resume upload updates durable candidate memory, profile input becomes
explicit, and JD entry becomes paste-first.

## Background

The first runnable stack is now on `master` with user profile, resume upload /
text parsing, and model-backed JD analysis. The July 31 review found that the
product still feels like a set of disconnected demos rather than a controllable
agent workflow:

- Uploading a resume extracts text but does not automatically build a useful
  candidate profile or structured resume facts.
- JD entry is still form-heavy; the desired workflow is paste raw JD text and
  parse structured fields automatically.
- Profile editing exposes a vague `constraints` JSON textarea. The user wants
  explicit required and optional text fields, all editable as text inputs rather
  than select controls.
- Agent runs can complete without visible frontend results even when rows exist
  in the database. During development, the user is willing to tolerate detailed
  UI noise because seeing the run process is more important than polish.

## Decisions

- Agent progress UI should show detailed engineering steps in development:
  context loading, resume/profile/JD loading, prompt/context construction, model
  call, model output validation, persistence, artifact generation, completion,
  and failure reasons.
- Do not expose hidden model chain-of-thought. Show durable workflow steps,
  sanitized inputs/outputs, metadata, errors, and persisted result IDs.
- Profile fields may all be text-entry fields. Do not use select-only controls
  for the v1 correction unless the field is already a stable boolean or system
  enum.
- The next coding sequence starts with housekeeping: archive prior completed
  planning/task artifacts before implementing new product fixes.

## Child Task Plan

1. `07-31-archive-previous-planning-artifacts` (P0)
   Archive completed predecessor planning/task artifacts and clear stale task
   pointers so this version starts from a clean Trellis state.

2. `07-31-agent-run-observability-result-visibility` (P0)
   Make JD analysis runs observable and make persisted results visible in the
   frontend. This directly fixes the current issue where the agent executed and
   database rows exist, but the UI does not reliably show results.

3. `07-31-resume-upload-auto-profile-parsing` (P1)
   After resume upload, automatically parse resume text into structured resume
   facts and a profile draft/update path.

4. `07-31-structured-profile-text-fields` (P1)
   Replace vague constraints editing with explicit required and optional text
   fields that are easy to fill when parsing cannot infer them.

5. `07-31-jd-paste-auto-parsing` (P2)
   Let users paste raw JD text and have the app parse structured job fields,
   reducing manual form friction.

## Cross-Task Acceptance Criteria

- [x] Prior completed planning/task artifacts are archived or explicitly left
  active with a reason.
- [x] A JD analysis run can be started and audited from the UI with visible
  intermediate steps and final status.
- [x] If analysis data exists in the database for a job, the job detail frontend
  can load and display it without requiring the current page session to have
  held the original POST response.
- [x] Resume upload produces durable parsed text and structured facts, and
  initiates a candidate profile draft/update path.
- [x] Profile editing uses explicit text-entry fields instead of a vague JSON
  constraints textarea.
- [x] JD creation supports paste-first parsing before or alongside manual
  correction.
- [x] All changes preserve user ownership boundaries through `get_current_user`
  and scoped repository queries.

## Out of Scope

- Job platform crawling, login, browser automation, or automatic submission.
- Automatic HR messaging.
- Complete customized resume export.
- Production streaming infrastructure unless the P0 task proves polling cannot
  satisfy development observability.
