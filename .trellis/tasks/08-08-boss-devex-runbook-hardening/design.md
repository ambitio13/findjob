# BOSS 本地开发体验与运行手册硬化 Design

## Boundaries

本任务只处理开发者操作面，不改业务决策。新增内容优先放在：

- `scripts/`：可执行入口。
- `docs/`：runbook 和故障解释。
- `README.md`：只保留短入口，不复制长流程。

## Proposed Commands

- `scripts/dev-health.sh`：检查 compose 服务、backend health、frontend 首页、worker 日志启动标记。
- `scripts/e2e-smoke.sh`：由 smoke 质量门任务提供或复用。
- `scripts/dev-logs.sh`：输出 backend/worker/frontend 最近日志，便于提交 bug 证据。
- `scripts/clean-smoke-artifacts.sh`：只清理固定前缀的 smoke/test 资源。

脚本必须使用明确目标，不允许 broad cleanup。Docker、Postgres、Redis 操作都要打印目标。

## Runbook Shape

建议新增 `docs/boss-local-dev-runbook.md`：

- Daily loop：启动、健康检查、运行 smoke、运行质量门。
- Failure map：症状 → 最可能原因 → 证据命令 → 修复动作。
- Bridge debugging：heartbeat、page_id、expected_url_hash、instruction_id、result。
- Data hygiene：smoke 用户、临时 DB、Redis namespace、resume upload temp dir。

## Compatibility

- 不替换 `docs/manual-boss-pilot.md`；该文件继续承担真实页面人工试点。
- 不要求引入 Makefile 或 Taskfile；除非后续证明确实能减少复杂度。
- 脚本默认服务端口沿用 `docker-compose.yml`。

## Risk

最大风险是“方便脚本”误删或误连业务资源。所有清理动作必须通过固定前缀和显式参数限制作用域。
