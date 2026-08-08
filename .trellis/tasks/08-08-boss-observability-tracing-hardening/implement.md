# BOSS 链路可观测性与追踪硬化 Implementation Plan

## Checklist

- [ ] 读取 backend `logging.md`、`error-handling.md`、`api-contracts.md`、`authentication.md`，
  frontend `api-integration.md`、`quality.md`。
- [ ] 梳理 BOSS inspect/match/prepare/execute/bridge/queue 当前日志事件和字段。
- [ ] 定义统一字段常量或轻量 helper，避免每处手写字段名漂移。
- [ ] 补齐 bridge instruction lifecycle 日志：enqueue/take/requeue/result/timeout。
- [ ] 补齐 queue worker failure 日志：namespace、job_id、agent_run_id、workflow_type。
- [ ] 检查前端 BOSS pilot 失败状态，确保能复制或看到 `agent_run_id`。
- [ ] 增加后端日志字段 contract 测试或错误 envelope 测试。
- [ ] 手工跑一次 bridge wrong-tab 和 inspect read_failed，确认日志能串起来。

## Validation

```bash
cd backend
./.venv/bin/ruff check app
./.venv/bin/pytest -q
```

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

```bash
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
```

## Rollback

优先回滚新增日志字段/helper 和前端失败展示，不回滚已有业务状态机。
