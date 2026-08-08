# BOSS 链路可观测性与追踪硬化

## Goal

统一 BOSS inspect、match、prepare、bridge、execute、queue 的日志字段和排障视图，确保真实链路
失败时能用 `agent_run_id`、`page_id`、`instruction_id`、`application_id` 和
`job_url_hash` 串起完整证据链。

## Background

- 后端已有 `structlog` 基础设施，多个服务会记录 `agent_run_id` 或 `request_id`。
- BOSS bridge 和 userscript channel 已有 heartbeat、next-instruction、result 相关日志。
- 对抗式审查中，worker 出现 `queue.resume_fact_extraction_missing_run`，原因可推断但跨 DB/queue
  证据不够集中。
- 真实 BOSS 页面验证留到最后做；在那之前需要让失败具备可解释性。

## Requirements

### R1. 统一追踪字段

BOSS 相关日志必须尽量携带稳定字段：`agent_run_id`、`workflow_type`、`user_id`、
`application_id`、`page_id`、`expected_url_hash`、`instruction_id`、`op`、`outcome`。

### R2. Bridge 生命周期可追踪

从 heartbeat、instruction enqueue/take/requeue、result receive 到 timeout，日志事件名和字段
必须可串联，且不记录原始 URL、简历内容、HR 消息正文等敏感信息。

### R3. Queue 失败可解释

worker `missing_run`、重试、handler failed 等关键路径应包含 queue namespace 和 job id，以便
区分测试污染、DB 不一致和真实业务失败。

### R4. 前端展示保留排障 ID

前端 BOSS pilot 或 Applications 页面在失败状态下应保留用户可复制的 `agent_run_id` 或相关
错误 ID，便于对照后端日志。

### R5. 不增加敏感数据暴露

日志和前端错误详情不得包含明文简历、完整职位 URL、cookie、token、HR 消息正文。

## Acceptance Criteria

- [ ] BOSS inspect/match/prepare/execute/bridge 关键日志具备统一字段集。
- [ ] bridge wrong-tab、timeout、result failure 均有可定位日志。
- [ ] worker `missing_run` 日志能区分 queue namespace、job id、agent_run_id。
- [ ] 前端失败状态能显示或保留 `agent_run_id`，不暴露敏感内容。
- [ ] 新增测试覆盖至少一个日志/错误 envelope 的关键字段 contract。
- [ ] backend `pytest/ruff` 与 frontend `lint/type-check/build` 通过。

## Notes

- 本任务是最终真实链路验收的前置安全网。
