# 推荐列表大循环 Implementation Plan

## Checklist

- [ ] 读取 backend `api-contracts.md`、`database.md`、`authentication.md`、`error-handling.md`、
  `performance.md`、`logging.md`，frontend `api-integration.md`、`state-management.md`、
  `components.md`、`quality.md`，shared `code-quality.md`。
- [ ] 确认当前只实现 `prepare_only` 批量半自动，不开放 auto-execute。
- [ ] 设计 batch run 输出 schema，优先复用 `AgentRun.result` 表达 per-item progress。
- [ ] 新增 backend schema：batch request、batch status、batch item status。
- [ ] 新增 backend service：串行运行 inspect → match → prepare，复用现有单职位服务。
- [ ] 新增 API：start/status/pause/resume；所有端点必须走 `X-User-Id` 用户边界。
- [ ] 增加 queue/worker handler 或同步小批量策略；若使用 worker，必须绑定 `agent_run_id` 和
  `QUEUE_NAMESPACE` 日志。
- [ ] 失败硬停止：连续 3 个 failed/unknown 停止；`skip/needs_review` 计入 item 结果但不算事故。
- [ ] 前端新增批量模式视图，默认只展示 prepare-only 进度和逐个审批入口。
- [ ] 测试覆盖：API 权限、prepare-only 正常、skip/needs_review、连续失败硬停止、pause/resume、
  auto-execute 未过门槛时被拒。

## Validation

```bash
cd backend
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/job_search_agent_test \
QUEUE_NAMESPACE=job-search-agent-test \
./.venv/bin/pytest app/tests/test_boss_recommended_jobs_api.py app/tests/test_boss_communicate_api.py -q
./.venv/bin/ruff check app
```

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

```bash
./scripts/e2e-smoke.sh
```

## Review Gates

- `mode=auto_execute` 默认不可用，并由后端拒绝，不依赖前端隐藏按钮。
- 批量流程不能并发触碰 userscript bridge。
- 所有 item 都能通过 `agent_run_id` 或 batch status 查到终态。
- 任何真实发送动作仍必须走 approve + execute。

## Rollback

移除 batch-loop API/前端 tab/worker handler 即可；单职位链路必须保持可用。
