# 10 次 dry-run 门槛验证 Design

## Purpose

本任务不是编码任务，而是最终启用 auto-execute 前的真实页面安全门。它验证已经完成的
userscript bridge、前端 pilot、可观测性和手册是否足够支撑真实 BOSS 受控交互。

## Preconditions

开始记录 10 次 dry-run 前必须满足：

- `08-03-boss-pilot-panel-frontend` 已归档。
- `08-08-boss-observability-tracing-hardening` 已归档。
- `08-08-boss-devex-runbook-hardening` 已归档。
- `./scripts/e2e-smoke.sh` 通过。
- `docker compose up -d --build` 使用正常本地 namespace 运行。
- userscript 已安装到浏览器并刷新到最新版本。

## Run Model

每次 dry-run 使用前端半自动面板执行：

1. 打开真实 BOSS 职位详情页。
2. 确认 bridge connected，记录 page_id。
3. inspect 当前职位。
4. match。
5. prepare，停在 `approval_required`。
6. 人工审阅 JD、match decision、opening message、风险/缺失项。
7. 只有确认无误时才人工 approve + execute。
8. 执行后人工核对 BOSS 页面结果，记录 outcome。

## Accident Definition

事故包括：

- wrong-tab：非目标 tab 执行了 instruction。
- duplicate 误发：已沟通过的职位被再次发送开场白。
- unexpected unknown：页面已经明确成功/重复/失败，但系统返回 unknown。
- hidden side effect：未点击 approve/execute 却触发真实发送。
- sensitive leak：日志/前端输出含 cookie/token/raw HTML/完整消息之外的敏感内容。

## Passing Rule

- 连续 10 次无事故。
- 至少 2 次 duplicate 场景。
- 所有记录包含日期、操作者、脱敏 URL/hash、page_id、agent_run_id、outcome、人工对账结论。
- 出现事故后计数归零，先开修复任务，修完后重新开始。

## Storage

记录写入本任务 `check.jsonl`。每行一个 JSON 对象，建议字段：

```json
{
  "date": "2026-08-08",
  "operator": "coldnight",
  "job_url_hash": "sha256:...",
  "page_id": "boss-tab-...",
  "agent_run_id": "run_...",
  "read_result": "succeeded",
  "external_result_status": "submitted",
  "accident": false,
  "manual_reconciliation": "BOSS page shows message sent"
}
```

## Out of Scope

- 不修代码。
- 不启用批量 auto-execute。
- 不把失败 dry-run 算作通过记录。
