# Resume Upload Auto Profile Parsing

## Goal

Automatically parse uploaded resumes into durable resume facts and a reviewable
profile draft, so the resume becomes useful long-term agent memory immediately
after upload — and so JD analysis can reason over structured candidate facts in
addition to raw text.

## Background

The current upload flow (`backend/app/api/v1/resumes.py:34-114`) extracts raw
text for `.txt`/`.pdf`/`.docx` via `backend/app/services/resume_parser.py` and
stores only parser telemetry in `ResumeVersion.parsed_facts` (the `_parser` and
`_parser_status` keys, `resume_parser.py:43-53`). There is no structured
candidate memory, no profile draft path, and JD analysis only ever sees raw text
plus the telemetry dict (`jd_analysis_service.py:113-124`; prompt at
`agents/prompts/jd_analysis.py:120-128`). The P0 observability task
(`07-31-agent-run-observability-result-visibility`, commit `057a4dc`) already
established the durable `AgentRun`/`AgentStep` audit pattern this task reuses.

## Requirements

- Uploading a parseable resume must trigger structured extraction after text
  extraction, running as an auditable `AgentRun`
  (`workflow_type="resume_fact_extraction"`).
- Extraction must produce a normalized, typed `facts` object stored in
  `ResumeVersion.parsed_facts` alongside the existing `_parser`/`_parser_status`
  telemetry:
  - contact (name / email / phone when available);
  - education;
  - work experiences;
  - projects;
  - skills;
  - years of experience;
  - target direction clues;
  - locations;
  - strengths and highlights;
  - uncertain / missing fields that need user confirmation.
- An `_extraction` status block (status, run_id, extracted_at, prompt_version,
  provider, model) must record the extraction outcome.
- The workflow must expose a reviewable profile draft derived from `facts`.
  Applying that draft to `UserProfile` is owned by the sibling
  `07-31-structured-profile-text-fields` task; this task must not silently
  overwrite user-entered `UserProfile` fields.
- A re-extract endpoint must allow re-running extraction on an existing resume
  version without re-uploading.
- Unsupported or sparse resumes must still produce a clear extraction status
  (`not_run` for unsupported, `succeeded` with `uncertain_fields` for sparse)
  and a recoverable next action.
- Model calls must go through `ModelGateway` (`models_gateway/base.py`); no
  direct provider calls. Extraction must mirror the JD-analysis executor +
  service pattern (`agents/jd_analysis_executor.py`,
  `services/jd_analysis_service.py:232-504`).
- JD analysis must consume the typed `facts` in addition to `raw_text`: update
  `_resume_to_dict` (`jd_analysis_service.py:113-124`) and the JD-analysis
  prompt (`agents/prompts/jd_analysis.py:86-197`) to reason over structured
  facts.
- All paths preserve user ownership boundaries (`get_current_user`, scoped
  queries; 404 on cross-user).

## Technical Notes

- No database migration: `parsed_facts` is already `JSON nullable`
  (`models.py:85`, migration `0001_initial.py:52-62`). New keys are additive;
  existing rows with only telemetry remain valid (`facts`/`_extraction`
  absent → treated as `not_run`).
- Extraction is executed inline within the upload request for v1 (as JD analysis
  is today, `jobs.py:171`). There is no background worker and no fake async
  completion: when upload returns, extraction has either succeeded or failed and
  the durable `AgentRun` / `AgentStep` trail is already readable.
- `FakeModelGateway` (`models_gateway/fake.py`) gains a route for the extraction
  prompt marker (mirroring `_JD_ANALYSIS_MARKER` at `fake.py:28`) for
  offline/test determinism.
- Field names in `facts` must coordinate with the sibling
  `07-31-structured-profile-text-fields` task so the draft maps cleanly to the
  future explicit profile fields. See `design.md` for the full `parsed_facts`
  shape and `implement.md` for the execution plan.

## Acceptance Criteria

- [ ] Uploading a supported resume creates raw text and structured `facts` in
  `parsed_facts`, with `_extraction.status = succeeded`.
- [ ] Uploading an unsupported format (e.g. `.rtf`) yields raw text empty and
  `_extraction.status = not_run` (no run created).
- [ ] A sparse resume still produces a valid `facts` object with
  `uncertain_fields` populated.
- [ ] The frontend shows extraction status (succeeded / failed /
  needs_confirmation / not_run) and renders the structured `facts`.
- [ ] User-entered `UserProfile` fields are never overwritten by extraction;
  this task only exposes a reviewable profile draft in resume facts.
- [ ] JD analysis receives and reasons over the typed `facts` in addition to
  `raw_text`; the `facts`-absent degradation path still works.
- [ ] A re-extract endpoint re-runs extraction on an existing version and
  refreshes `facts`.
- [ ] Failed extraction persists a failed `AgentRun` + `AgentStep` (sanitized,
  no raw resume text). Upload still returns the saved resume with failed
  extraction status; explicit re-extract may return 502.
- [ ] Tests cover supported upload, sparse extraction, unsupported format,
  model-invalid failure, re-extract, profile draft exposure, JD-analysis fact
  consumption, and user ownership.

## Out of Scope

- A background worker / queue (extraction runs inline for v1, matching JD
  analysis today).
- New database columns or a separate profile-drafts table (draft lives in
  `parsed_facts.facts`).
- Applying the profile draft into `UserProfile` and redesigning the profile
  form UI (owned by the sibling `07-31-structured-profile-text-fields` task);
  this task only provides the draft source.

## Notes

- Depends on the P0 agent-observability patterns (`AgentRun`/`AgentStep`) that
  are now in place; extraction reuses them for auditability.
