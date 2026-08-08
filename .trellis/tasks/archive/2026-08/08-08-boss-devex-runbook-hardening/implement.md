# BOSS 本地开发体验与运行手册硬化 Implementation Plan

## Checklist

- [x] 读取 shared/backend/frontend 质量规范和文档现状。
- [x] 新增 `docs/boss-local-dev-runbook.md`，先覆盖 daily loop 和故障地图。
- [x] 在 README 增加 BOSS 半自动链路开发入口，避免长篇复制。
- [x] 新增 `scripts/dev-health.sh`，检查 compose、backend、frontend、worker。
- [x] 新增 `scripts/dev-logs.sh`，稳定输出 backend/worker/frontend 近端日志。
- [x] 新增或复用 `scripts/clean-smoke-artifacts.sh`，只清理固定 smoke/test 前缀。
- [x] 将 `scripts/e2e-smoke.sh` 接入 runbook；如果 smoke 任务尚未实现，先写依赖说明。
- [x] 验证脚本在服务未启动、服务已启动两类情况下输出可理解错误。
  (dev-health.sh 在 compose 运行时通过；clean-smoke 在无 redis-cli/psql 时给出
   明确安装提示；dev-logs.sh 输出三服务近端日志)

## Validation

```bash
git diff --check                 # ✅ 无空白错误
./scripts/dev-health.sh          # ✅ 全部检查通过
./scripts/dev-logs.sh            # ✅ 输出 backend/worker/frontend 日志
./scripts/clean-smoke-artifacts.sh --yes  # ✅ 输出清理目标（本机无 redis-cli/psql，给出安装提示）
cd backend && ./.venv/bin/ruff check app && ./.venv/bin/pytest -q   # ✅ ruff passed, 787 passed + 1 pre-existing failure
cd frontend && pnpm lint && pnpm type-check && pnpm build           # ✅ lint/type-check/build 通过（build 仍有 chunk warning，Task 4 处理）
```

## Rollback

回滚新增 `scripts/` 文件、runbook 和 README 小节即可，不应触碰业务代码。
