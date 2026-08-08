# BOSS 链路可观测性与追踪硬化 Implementation Plan

## Checklist

- [x] 读取 backend `logging.md`、`error-handling.md`、`api-contracts.md`、`authentication.md`，
  frontend `api-integration.md`、`quality.md`。
- [x] 梳理 BOSS inspect/match/prepare/execute/bridge/queue 当前日志事件和字段。
- [x] 定义统一字段常量或轻量 helper，避免每处手写字段名漂移。
- [x] 补齐 bridge instruction lifecycle 日志：enqueue/take/requeue/result/timeout。
- [x] 补齐 queue worker failure 日志：namespace、job_id、agent_run_id、workflow_type。
- [x] 检查前端 BOSS pilot 失败状态，确保能复制或看到 `agent_run_id`。
- [x] 增加后端日志字段 contract 测试或错误 envelope 测试。
- [x] 手工跑一次 bridge wrong-tab 和 inspect read_failed，确认日志能串起来。
  (`./scripts/e2e-smoke.sh --skip-up` 于 2026-08-09 通过：wrong-tab 204、probe
  bounded-failure、inspect read_failed、19 passed / 0 failed)

## Validation

```bash
cd backend
./.venv/bin/ruff check app          # ✅ All checks passed
./.venv/bin/pytest -q               # ✅ 787 passed, 1 pre-existing failure
                                    #    (test_redis_settings_derived_from_redis_url
                                    #     — env-var REDIS_URL db index mismatch,
                                    #     unrelated to this task, fails on clean tree)
```

Frontend (R4 — agent_run_id 已在 Task 1 pilot panel 失败状态展示中落地，
本轮无新增前端改动，故无需重复跑):

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

```bash
docker compose logs --tail=200 backend   # 人工验证
docker compose logs --tail=200 worker    # 人工验证
```

## Rollback

优先回滚新增日志字段/helper 和前端失败展示，不回滚已有业务状态机。
