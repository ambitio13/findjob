# BOSS 本地开发体验与运行手册硬化

## Goal

把 BOSS 半自动链路的本地开发、测试、smoke、日志排障和残留清理收敛成清晰的一键入口与
runbook，让后续开发者不需要从 README、手工 curl、Docker 日志和 Trellis 任务里拼流程。

## Background

- 根目录目前没有 `scripts/` 目录；README 只列出了基础 `docker compose up --build`、
  backend `pytest/ruff`、frontend `pnpm` 命令。
- `docs/manual-boss-pilot.md` 和 `docs/boss-communicate-testing-plan.md` 记录了人工试点和
  测试路线，但缺少“开发时每天怎么跑、失败怎么定位、残留怎么清”的短路径。
- 2026-08-08 对抗式审查中，手工执行了 compose、health、frontend、backend tests、
  bridge round-trip 和 inspect bounded failure；这些动作应该沉淀成可重复工具。

## Requirements

### R1. 一键开发入口

新增或整理脚本入口，覆盖本地启动、健康检查、质量门、E2E smoke 和日志查看。命令命名应短、
稳定、能被 README 引用。

### R2. 排障 runbook

新增 BOSS 本地开发 runbook，按症状组织：

- 后端/worker/Redis/Postgres 未就绪。
- userscript bridge 未连接。
- wrong-tab 或 page hash 不匹配。
- inspect 长时间等待或返回 `read_failed`。
- worker 出现 `missing_run`。
- 前端 pilot panel 状态和后端状态不一致。

### R3. 残留清理规约

文档必须说明哪些残留可以自动清理，哪些必须人工确认。清理命令必须限制在 smoke/test 前缀，
不得使用 `flushdb`、宽泛 delete 或无约束 SQL。

### R4. 不改变业务行为

本任务只改善本地开发体验和文档入口，不改变 BOSS 沟通业务逻辑、自动执行策略或真实平台行为。

## Acceptance Criteria

- [ ] README 增加 BOSS 本地开发短路径，链接到具体 runbook。
- [ ] 新增或更新 runbook，包含启动、验证、排障、清理四个部分。
- [ ] 所有新增脚本支持失败即退出，并输出下一步排障提示。
- [ ] 清理步骤只作用于明确的 smoke/test 前缀资源。
- [ ] 开发者可按文档从空环境跑到 backend/frontend/worker/bridge smoke 可验证状态。
- [ ] 质量门通过：`git diff --check`，backend `ruff/pytest`，frontend `lint/type-check/build`。

## Notes

- 依赖 `08-08-boss-e2e-smoke-test-isolation` 提供 smoke 入口；如果该任务尚未完成，本任务可先写文档框架和命令占位。
