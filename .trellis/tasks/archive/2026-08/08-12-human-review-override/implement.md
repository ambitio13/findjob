# Implement — needs_review 人工审阅覆写路径

> 执行计划。先后端契约、再前端接入、最后真实链路验证。
> 前置阅读：`prd.md`、`design.md`。

## Phase A：后端覆写契约（PRD R1 + R2）

> ✅ 代码完成（后端 986 passed + ruff 全绿）。实际编码在
> `08-12-jd-capture-fields-and-review-draft` 任务期间一并完成；PRD R6
> （skip 也开放覆写）也在那里补齐。

- [x] A1. `schemas/boss_communicate.py`：新增 `HumanReviewOverride`，
      `CommunicatePrepareRequest` 加可选 `human_review`。
- [x] A2. `services/boss_communicate_service.py`：`prepare_communicate_action`
      加 `human_review` 关键字参；needs_review 分支（422 矩阵 + 消息复验 +
      outgoing_text 取人工消息）；source_snapshot 追加 human_review 审计键。
- [x] A3. `services/boss_match_service.py`：`_execute_boss_match` 返回门前
      原始消息；`schemas/boss_match_decision.py` 的 `MatchDecisionOut` 加
      `draft_opening_message`；路由透传两处新字段。
- [x] A4. 测试：覆写正向（AC1）+ 422 矩阵（AC2）+ draft 字段（AC3）+
      无覆写回归（AC5）。

验证：`cd backend && set -a && source .env.test && export QUEUE_NAMESPACE=job-search-agent-test && set +a && .venv/bin/python -m pytest -q && .venv/bin/ruff check .`

## Phase B：前端人审区（PRD R3）

> ✅ 代码完成（前端 lint/type-check/build 全绿）。

- [x] B1. `types` + `api/client.ts`：`draft_opening_message`、
      `prepareCommunicate` 可选 humanReview 参数。
- [x] B2. `RecommendedJobPilotPanel.tsx`：needs_review 拦截块内可展开人审区
      （草稿预填 textarea + 担责勾选 + 「人工审阅后继续」按钮）；成功后汇入
      approve 步骤；semiAuto 时不渲染。
- [x] B3. 文案与错误展示按 design.md §3；确认 communicate 路径零回归。

验证：`cd frontend && pnpm lint && pnpm type-check && pnpm build`

## Phase C：真实链路验证与收尾（AC7 + 规约同步）

> 规约同步（C2）已完成：evolution-contracts.md §12「Human Review Override」
> 已落地。AC1–AC6 已在 prd.md 勾选，后端 986 passed + ruff 全绿、前端
> lint/type-check/build 全绿已验证（08-13 复跑）。
> **仅剩 C1（真实 BOSS 页面验证）需要用户现场操作。**

- [ ] C1. 用户用一条 needs_review 真实职位走 人审→prepare→approve→execute
      前置；记录写入 check.jsonl（dry-run 记录可从此正常积累）。
- [x] C2. trellis-update-spec：evolution-contracts 增补「Human Review
      Override」契约节。（已完成，见 §12）
- [x] C3. 逐条勾选 prd.md AC1–AC6。（AC7 仍待 C1 完成后勾选）

## Review Gates

- Phase A 后：后端契约自查（422 矩阵 + 审计键 + 回归全绿）。
- Phase C 后：trellis-check + 用户验收；此后 dry-run 门槛任务恢复积累。

## 全局验证

```bash
cd backend && set -a && source .env.test && export QUEUE_NAMESPACE=job-search-agent-test && set +a && .venv/bin/python -m pytest -q && .venv/bin/ruff check .
cd frontend && pnpm lint && pnpm type-check && pnpm build
```
