# BOSS 本地开发体验与运行手册硬化 Implementation Plan

## Checklist

- [ ] 读取 shared/backend/frontend 质量规范和文档现状。
- [ ] 新增 `docs/boss-local-dev-runbook.md`，先覆盖 daily loop 和故障地图。
- [ ] 在 README 增加 BOSS 半自动链路开发入口，避免长篇复制。
- [ ] 新增 `scripts/dev-health.sh`，检查 compose、backend、frontend、worker。
- [ ] 新增 `scripts/dev-logs.sh`，稳定输出 backend/worker/frontend 近端日志。
- [ ] 新增或复用 `scripts/clean-smoke-artifacts.sh`，只清理固定 smoke/test 前缀。
- [ ] 将 `scripts/e2e-smoke.sh` 接入 runbook；如果 smoke 任务尚未实现，先写依赖说明。
- [ ] 验证脚本在服务未启动、服务已启动两类情况下输出可理解错误。

## Validation

```bash
git diff --check
./scripts/dev-health.sh
./scripts/dev-logs.sh
cd backend && ./.venv/bin/ruff check app && ./.venv/bin/pytest -q
cd frontend && pnpm lint && pnpm type-check && pnpm build
```

## Rollback

回滚新增 `scripts/` 文件、runbook 和 README 小节即可，不应触碰业务代码。
