# Implement: BOSS 推荐职位 JD 读取与自动沟通 Agent

This is an implementation plan only. Do not start coding until the user starts
this task or assigns subtasks to coding agents.

## Subtask 1: Bridge Page Binding

Goal: prevent wrong-tab and stale-page actions before adding JD read.

Files likely touched:

- `backend/app/platforms/boss/userscript_channel.py`
- `backend/app/schemas/userscript_bridge.py`
- `backend/app/api/v1/userscript_bridge.py`
- `docs/boss-userscript.user.js`
- `backend/app/tests/test_userscript_channel.py`
- `backend/app/tests/test_userscript_bridge_api.py`

Deliverables:

- Generate a stable per-tab `page_id` in the userscript.
- Heartbeat includes `page_id`, `page_url_hash`, `page_title`.
- Channel stores active page metadata.
- Instructions include target `page_id` and expected URL hash.
- Results include `page_id`; mismatches are rejected.
- Frontend status can show connected page title/hash without raw URL.

Acceptance:

- Multiple simulated page IDs cannot consume each other's instructions.
- URL hash mismatch blocks `read_jd`, click, fill and send.
- Heartbeat timeout clears active page safety state.

Validation:

```bash
cd backend && uv run pytest -q app/tests/test_userscript_channel.py app/tests/test_userscript_bridge_api.py
cd backend && uv run ruff check app
git diff --check
```

## Subtask 2: Scoped JD Read Instruction

Goal: add a safe exception to read current BOSS JD text without reading raw HTML.

Files likely touched:

- `backend/app/platforms/boss/userscript_channel.py`
- `backend/app/platforms/boss/userscript_adapter.py`
- `backend/app/platforms/boss/sanitizer.py`
- `backend/app/schemas/userscript_bridge.py`
- `docs/boss-userscript.user.js`
- `backend/app/tests/test_userscript_boss_adapter.py`
- `backend/app/tests/test_boss_sanitizer.py`

Deliverables:

- Add `read_jd` instruction/result schema.
- Userscript extracts scoped fields from BOSS recommended card/detail page.
- Backend validates JD shape and length.
- Backend rejects raw HTML-like payloads and obvious secret patterns.
- Adapter exposes `read_current_jd()` or equivalent platform method.

Acceptance:

- `read_jd` returns structured fields and `page_url_hash`.
- Missing title/description yields `jd_too_sparse`.
- Raw `<script>`, cookies, token-like values are stripped or rejected.
- Existing sanitized diagnostic result rules remain unchanged for other ops.

Validation:

```bash
cd backend && uv run pytest -q app/tests/test_userscript_boss_adapter.py app/tests/test_boss_sanitizer.py
cd backend && uv run ruff check app
```

Manual check:

- Install userscript.
- Open one BOSS recommended/detail page.
- Trigger JD read from backend.
- Confirm no raw URL/HTML/cookie/token is stored or logged.

## Subtask 3: Job/Application Upsert From Browser JD

Goal: turn the browser-read JD into normal product data with provenance.

Files likely touched:

- backend application/job service files
- backend schemas for applications/jobs
- frontend application/job detail surfaces
- tests around user scoping and duplicate source handling

Deliverables:

- Create or update a Job from `platform=boss + page_url_hash`.
- Create or attach an ApplicationRecord for the selected resume/user.
- Store source metadata: `source_kind=boss_userscript_read_jd`,
  `page_url_hash`, `read_at`, `agent_run_id`.
- Reuse existing JD analysis pipeline where possible.

Acceptance:

- Re-reading the same page does not create duplicate jobs unless source hash changes deliberately.
- Cross-user access is impossible.
- Source changes mark previous generated artifacts/action previews stale.

Validation:

```bash
cd backend && uv run pytest -q
cd backend && uv run ruff check app
```

## Subtask 4: Match Decision And Opening Message

Goal: make the backend decide whether this JD is worth contacting.

Files likely touched:

- backend agent/prompt modules
- backend generated artifact services
- prompt abstraction files if present
- tests for fake/model output validation

Deliverables:

- Add structured match output schema:
  `communicate | skip | needs_review`.
- Inputs include JD, profile/preferences, resume version and blacklist/filters.
- Output includes score, reasons, risks, missing requirements and opening message.
- Store prompt version/model/source IDs.
- Low confidence and validation errors stop before browser side effects.

Acceptance:

- Fake provider tests cover communicate/skip/needs_review.
- Invalid model JSON never becomes a communicate decision.
- Opening message length/tone/PII rules are validated.

Validation:

```bash
cd backend && uv run pytest -q app/tests
cd backend && uv run ruff check app
```

## Subtask 5: Communication Action Draft, Approval And Idempotency

Goal: prepare a side-effect action before any click.

Files likely touched:

- backend application action model/service
- backend API routes for approve/execute
- frontend approval UI
- tests around idempotency and stale payloads

Deliverables:

- Action type `boss_immediate_communicate`.
- Payload preview with exact opening message.
- `payload_hash` and external idempotency key.
- Approval required by default.
- Stale source/payload invalidates approval.

Acceptance:

- Unapproved execute returns 409/approval-required.
- Same idempotency key cannot send twice.
- Payload changes require reapproval.
- Timeline records draft, approval, running and terminal states.

Validation:

```bash
cd backend && uv run pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
```

## Subtask 6: Execute Immediate Communicate

Goal: perform the bounded browser side effect after all gates pass.

Files likely touched:

- `backend/app/platforms/boss/userscript_adapter.py`
- `docs/boss-userscript.user.js`
- backend service/API execute path
- frontend guided panel
- adapter tests and API tests

Deliverables:

- `click_immediate_communicate` instruction.
- `fill_opening_message` instruction with correct textarea/contenteditable setter.
- `send_opening_message` instruction, exactly once.
- `read_communication_result` instruction.
- Terminal result mapping: `succeeded | duplicate | failed | unknown`.

Acceptance:

- No click is sent before approval/idempotency passes.
- A single execute call emits at most one immediate-communicate click and one send click.
- `unknown` result stops and asks for manual reconciliation.
- Duplicate platform marker maps to duplicate, not failure.

Validation:

```bash
cd backend && uv run pytest -q app/tests/test_userscript_boss_adapter.py
cd backend && uv run pytest -q
cd frontend && pnpm lint && pnpm type-check && pnpm build
```

## Subtask 7: Manual Pilot Runbook And Metrics Gate

Goal: make real BOSS testing repeatable and conservative.

Files likely touched:

- `docs/manual-boss-pilot.md`
- `.trellis/tasks/08-03-boss-jd-read-auto-communicate-agent/check.jsonl`

Deliverables:

- Runbook for one-job read/match/communicate pilot.
- Dry-run checklist.
- Real-send checklist.
- Manual reconciliation procedure.
- Metrics gate before any larger loop:
  - 10 dry-runs;
  - 3 real approved sends;
  - 0 wrong-tab actions;
  - 0 duplicate sends;
  - every unknown manually reconciled.

Acceptance:

- Another coding agent can follow the runbook without asking for hidden context.
- The larger recommended-job loop remains blocked until metrics gate passes.

## Final Task Validation

```bash
cd backend && uv run ruff check app
cd backend && uv run pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 ./.trellis/scripts/task.py validate 08-03-boss-jd-read-auto-communicate-agent
git diff --check
```

## Parallelization Notes

- Subtask 1 must happen before Subtasks 2 and 6.
- Subtask 2 must happen before Subtasks 3 and 4.
- Subtasks 3 and 4 can run mostly in parallel once the JD schema is stable.
- Subtask 5 should start after the action shape in Subtask 4 is known.
- Subtask 6 depends on Subtasks 1 and 5.
- Subtask 7 can start early but must be finalized after Subtask 6.

## Non-Goals

- No automatic recommendation-list loop in this task.
- No CAPTCHA solving.
- No CDP primary path.
- No pure userscript decision engine.
