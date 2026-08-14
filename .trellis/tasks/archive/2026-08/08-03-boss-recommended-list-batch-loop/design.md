# 推荐列表大循环 Design

## Scope Decision

本任务采用渐进式实现：

1. 第一阶段只实现“批量半自动”：遍历推荐职位，串行执行 inspect → match → prepare，并停在
   `approval_required` 或 `needs_review`，不自动 approve，不自动 execute。
2. 第二阶段只在 `08-03-boss-dry-run-gate` 通过后启用 auto-execute。代码可以预留配置门，
   但默认必须关闭，且没有门槛证据时不可被前端打开。

这样可以让工程实现先落地，同时不提前扩大真实 BOSS 外部副作用。

## Architecture

推荐以 backend durable batch run 为中心：

- 新增 batch run API：创建一个批量运行记录，返回 `agent_run_id` 或 batch id。
- Worker/服务层串行处理每个职位，不并发触碰 userscript bridge。
- 前端只负责启动、轮询、暂停/恢复、展示每个 item 的状态。

批量 item 的最小状态：

- `pending`
- `inspecting`
- `matched`
- `prepared`
- `needs_review`
- `skipped`
- `failed`
- `stopped`
- `executed`（仅 auto-execute 门槛通过后可能出现）

## API Contract

建议新增端点：

- `POST /api/v1/boss/recommended-jobs/batch-loop`
- `GET /api/v1/boss/recommended-jobs/batch-loop/{run_id}`
- `POST /api/v1/boss/recommended-jobs/batch-loop/{run_id}/pause`
- `POST /api/v1/boss/recommended-jobs/batch-loop/{run_id}/resume`

请求体建议：

```json
{
  "resume_version_id": "rv_...",
  "limit": 10,
  "mode": "prepare_only"
}
```

`mode=auto_execute` 必须被后端配置门拒绝，直到 dry-run gate 通过。

## Safety

- 单 active page/application，不并发发送 bridge instruction。
- 每个职位都复用单职位 inspect/match/prepare/execute 服务，不复制业务规则。
- `skip`、`needs_review`、`failed`、`unknown` 不自动 execute。
- 连续失败阈值默认 3；达到阈值后硬停止。
- 所有外部副作用仍必须经过已有 approval/idempotency guard。

## Persistence

优先复用 `AgentRun` + `AgentStep` 记录批量流程。若现有 schema 不能表达 per-item 状态，
可以将 per-item progress 存入 `AgentRun.result` 的结构化 JSON；只有当查询需求明确时再新增表。

## Frontend

在 BOSS pilot 区域增加“批量模式”视图：

- 当前运行状态和进度。
- 每个职位 item 的状态、决策、action id、错误分类。
- 暂停/恢复按钮。
- 对 prepared item 提供逐个 approve/execute 入口。

## Dependencies

- `08-03-boss-pilot-panel-frontend` 完成后，前端有单职位操作基础。
- `08-08-boss-observability-tracing-hardening` 完成后，批量排障更可靠。
- `08-03-boss-dry-run-gate` 通过前，不启用 auto-execute。

## Rollback

保留单职位端点不变；如批量路径异常，关闭 batch-loop 入口即可。不要回滚单职位 inspect/match/prepare/execute。
