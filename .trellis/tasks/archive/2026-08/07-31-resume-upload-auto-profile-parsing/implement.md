# Resume Upload Auto Profile Parsing — Implementation Plan

Execution order is top-to-bottom. Each section ends with a validation gate; do
not proceed to the next until the gate passes. Run all backend commands from
`backend/`.

## 1. Typed facts contract + fake gateway route

- [x] Create `backend/app/schemas/resume_facts.py`:
      `ResumeFactsModelOutput` with optional typed sub-models
      (`Contact`, `EducationItem`, `WorkExperienceItem`, `ProjectItem`,
      `UncertainField`) and fields per `design.md` "parsed_facts shape".
      Every field optional/default-empty; `uncertain_fields: list[UncertainField]`.
- [x] Add a prompt marker constant + a deterministic fake output in
      `backend/app/models_gateway/fake.py` mirroring the
      `_JD_ANALYSIS_MARKER` / `_JD_ANALYSIS_FAKE_OUTPUT` pattern (`fake.py:28`,
      `:89-109`). Route extraction prompts to it.
- [x] **Gate**: `python3 -m pytest app/tests/test_model_gateway.py -q` passes;
      new fake route returns a valid `ResumeFactsModelOutput`.

## 2. Extraction executor + prompt

- [x] Create `backend/app/agents/prompts/resume_fact.py`:
      `build_resume_fact_messages(raw_text, filename)` — system prompt
      (extraction assistant, no fabrication, mark uncertain), user message
      (schema JSON + capped `raw_text`, constant
      `RESUME_RAW_TEXT_CAP` mirroring `agents/prompts/jd_analysis.py:24`).
- [x] Create `backend/app/agents/resume_fact_executor.py`:
      `ResumeFactExecutor(gateway)` with `build_prompt` / `call_model` /
      `validate`, raising `ResumeFactValidationError(kind)` mirroring
      `jd_analysis_executor.py:114-151`.
- [x] **Gate**: unit test the executor with the fake gateway — valid output
      validates; malformed JSON / schema violations raise the right error kind.

## 3. Extraction service orchestration (AgentRun trail)

- [x] Create `backend/app/services/resume_fact_service.py` with
      `WORKFLOW_TYPE = "resume_fact_extraction"` and
      `extract_resume_facts(resume, version, gateway, db) -> AgentRun`,
      mirroring `run_resume_aware_jd_analysis`
      (`jd_analysis_service.py:232-504`):
      steps `load_context`, `build_prompt_context`, `call_model`,
      `validate_model_output`, `persist_outputs`, `complete_run`.
- [x] `_fail_run` helper mirrors `jd_analysis_service.py:277-307` for
      persistence (failed step + run, sanitized, commit). Upload callers should
      receive the saved resume with `_extraction.status=failed`; only the
      explicit re-extract endpoint should translate extraction failure to 502.
- [x] `persist_outputs` writes `parsed_facts.facts` (validated object) +
      `parsed_facts._extraction` (status/run_id/extracted_at/prompt_version/
      provider/model); preserves existing `_parser`/`_parser_status`.
- [x] **Gate**: new `test_resume_fact_service.py` covering success (asserts
      6 ordered step names, sanitized results, `_extraction.status=succeeded`,
      `facts` present, `SUPER_SECRET_RESUME_TOKEN` never in persisted rows),
      model-invalid (failed run persisted, 502), gateway-error (failed run),
      sparse resume (valid object + `uncertain_fields`), unsupported format
      (run not created, `status=not_run`).

## 4. API: upload trigger + re-extract + apply-draft

- [x] `POST /api/v1/resumes` (`resumes.py:34-114`): add
      `gateway: ModelGateway = Depends(get_model_gateway_dep)`; after
      `resume_repo.create_version(...)` + `db.commit()`, call
      `resume_fact_service.extract_resume_facts(...)` inline; return the saved
      resume resource with the final extraction status and run id. Unsupported
      format uploads skip extraction (`_extraction.status=not_run`). Model
      extraction failure must not make an already-saved upload look like a file
      upload failure; return the resume with `_extraction.status=failed`.
- [x] `POST /api/v1/resumes/{resume_id}/versions/{version_id}/extract`:
      re-extract on existing `raw_text`; user-scoped 404 on cross-user; returns
      502 on model/provider/schema failure after persisting failed run details.
- [x] Update response schemas to surface extraction status (reuse
      `ResumeVersionOut.parsed_facts`; optionally add
      `extraction_status`/`extraction_run_id` derived fields).
- [x] **Gate**: extend `app/tests/test_resume_upload.py` — upload now yields
      `parsed_facts.facts` + `_extraction.status=succeeded`; `.rtf`/unsupported
      yields `status=not_run`; raw_text-still-never-logged invariant holds;
      user scoping holds. Add tests for re-extract and profile draft exposure.
      Add e2e test: upload with raising gateway still returns 201 +
      `_extraction.status=failed`.

## 5. JD analysis consumes structured facts

- [x] `_resume_to_dict` (`jd_analysis_service.py:113-124`): expose
      `facts = parsed_facts.get("facts") or {}` as a clean `facts` key.
- [x] Prompt builder `build_resume_fact_messages` /
      `build_jd_analysis_messages` (`agents/prompts/jd_analysis.py:86-197`):
      render typed `facts` in the `## RESUME` section; update instructions to
      reason over facts in addition to `raw_text`; bump prompt version in
      provenance metadata.
- [x] Update / extend `app/tests/test_jd_analysis_contracts.py` prompt +
      context-loader tests for the new `facts` shape and the
      `facts`-absent degradation path (`test_load_context_handles_none_parsed_facts`
      and the `{"skills":["Go"]}` case at `:558-646`).
- [x] **Gate**: `python3 -m pytest app/tests/test_jd_analysis_contracts.py
      app/tests/test_jd_analysis_api.py -q` passes.

## 6. Frontend: extraction status + facts + draft review

- [x] `frontend/src/pages/resumes/ResumeDetailPage.tsx`: show extraction
      status from `parsed_facts._extraction.status` (succeeded/failed/
      needs_confirmation/not_run), render `parsed_facts.facts` as structured
      read-only cards (contact, education, work, projects, skills,
      years_of_experience, target_direction, locations, strengths, highlights,
      uncertain_fields). Add a "重新解析" button calling the re-extract
      endpoint.
- [x] Profile draft review: surface draft fields derived from `facts` as a
      read-only preview. The apply/merge action belongs to
      `07-31-structured-profile-text-fields`.
- [x] API client `frontend/src/api/client.ts`: add
      `reextractResumeFacts(resumeId, versionId)`.
- [x] **Gate**: `cd frontend && npm run lint && npm run type-check && npm run build`
      pass.

## 7. Full quality gate

- [x] `cd backend && python3 -m pytest -q` — all green (113 passed).
- [x] `cd backend && ruff check app && ruff format --check app` — all green.
- [x] `cd frontend && npm run lint && npm run type-check && npm run build`.
- [ ] Manual smoke: upload a `.txt`/`.pdf`/`.docx` resume → see structured
      facts + extraction status; upload `.rtf` → `not_run`; re-extract; run JD
      analysis and confirm it references structured facts; confirm the profile
      draft preview is visible but does not overwrite `UserProfile`.
- [x] Run `trellis-check` skill before commit.

## Risky files / rollback points

- `backend/app/api/v1/resumes.py` — upload path gains a model call; a regression
  here breaks all uploads. Rollback: guard extraction behind a settings flag
  and skip on failure (resume + raw text still returned).
- `backend/app/services/jd_analysis_service.py` + `agents/prompts/jd_analysis.py`
  — JD-analysis prompt change can shift model behavior. Rollback: prompt
  version bump is reversible; keep the prior instruction text in git history.
- `backend/app/models_gateway/fake.py` — adding a route; low risk, but a wrong
  marker breaks offline/test extraction.

## Follow-up checks before `task.py start`

- [ ] `implement.jsonl` / `check.jsonl` already hold real spec entries (verified
      pre-planning); no seed-only rows.
- [ ] `design.md` + `implement.md` reviewed against `prd.md` acceptance
      criteria; every AC has a corresponding implementation section.
- [ ] Coordinate field names in `facts` with the sibling
      `07-31-structured-profile-text-fields` task so the draft maps cleanly to
      the future explicit profile fields.
