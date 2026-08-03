# PRD: BOSS JD 读取与自动沟通实现

## Parent

`08-01-post-mvp-application-readiness-loop`

## Source Plan

`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/implement.md`

Subtask 1 (Bridge Page Binding) is already completed and committed (`d2d05b7`).

## Goal

Implement Subtasks 2–7 from the archived plan:

1. **Subtask 2: Scoped JD Read Instruction** — `read_jd` op, userscript extraction, backend validation, sanitizer rules
2. **Subtask 3: Job/Application Upsert From Browser JD** — turn browser-read JD into product data with provenance
3. **Subtask 4: Match Decision And Opening Message** — structured match output (communicate/skip/needs_review) + opening message
4. **Subtask 5: Communication Action Draft, Approval And Idempotency** — `boss_immediate_communicate` action type, payload_hash, idempotency key
5. **Subtask 6: Execute Immediate Communicate** — bounded browser side effect after all gates pass
6. **Subtask 7: Manual Pilot Runbook And Metrics Gate** — runbook + dry-run/real-send checklists

## Acceptance Criteria

All acceptance criteria from the source plan's Subtasks 2–7 apply.

Final task validation:

```bash
cd backend && uv run ruff check app
cd backend && uv run pytest -q
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build
git diff --check
```

## Security Invariants (all preserved)

- 一次调用只处理一个 application_id
- prepare 永不点击最终提交
- submit 必须先过外部幂等键 + assert_action_approved
- 永不持久化 credentials/cookies/tokens/原始 HTML/原始 JD/原始简历
- CAPTCHA、限流、选择器漂移、模糊页面状态 = 硬停止
- 后端发指令，油猴只执行
- read_jd is the only raw page text exception; raw HTML remains forbidden
- A single external communication action may click at most: one click_immediate_communicate; one send_opening_message
- Unknown result never triggers automatic retry
- No instruction may click unless a backend action is already approved
