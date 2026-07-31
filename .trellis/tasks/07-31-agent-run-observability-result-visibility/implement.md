# Agent Run Observability and Result Visibility Implementation Plan

## Phase 1: Backend Read Contracts

- Inspect `AgentRunOut`, `AgentStep`, `JobAnalysisOut`, and artifact schemas.
- Add or extend schemas for run detail with ordered steps.
- Add a user-scoped endpoint for run steps/detail if existing response cannot
  carry steps cleanly.
- Extend job analysis list/read response so persisted rows provide enough data
  for the frontend result view.

## Phase 2: Workflow Step Granularity

- Refine `run_resume_aware_jd_analysis` step persistence around context loading,
  prompt/context construction, model call, validation, output persistence, and
  completion.
- Store sanitized metadata only: counts, IDs, parser/model/prompt version,
  validation status, elapsed timing, and error type/message.
- Ensure failures persist a failed run and failed step with a `run_id` available
  to the API caller where practical.

## Phase 3: Frontend Result Hydration

- Update `JobDetailPage` to call `listJobAnalyses` on page load.
- Render the latest persisted analysis result when present.
- After `runJdAnalysis`, refresh persisted analyses instead of relying only on
  local POST state.
- Add an agent process panel that fetches and displays ordered run steps for the
  selected result/run.
- Add explicit empty, running, success, and failed states.

## Phase 4: Tests

- Backend:
  - analysis list/read includes enough persisted metadata to reconstruct the UI;
  - run steps are ordered and user-scoped;
  - failure runs are inspectable but sanitized;
  - cross-user access returns 404 or empty lists as appropriate.
- Frontend:
  - type-check all new response contracts;
  - add component/API tests if the project test harness supports them, otherwise
    rely on lint/type/build and backend contract tests.

## Validation Commands

```bash
cd backend && .venv/bin/ruff check app
cd backend && .venv/bin/ruff format --check app
cd backend && .venv/bin/pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
python3 .trellis/scripts/task.py validate 07-31-agent-run-observability-result-visibility
```

## Rollback Points

- Keep schema additions backward-compatible so frontend can degrade on older run
  rows.
- If step endpoint scope is wrong or too broad, revert that endpoint before
  merging; observability must not trade away user isolation.
