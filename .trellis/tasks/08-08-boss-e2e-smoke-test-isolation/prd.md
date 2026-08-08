# BOSS E2E smoke 与测试隔离质量门

## Goal

建立一套不会污染本地业务环境的全链路验证质量门，让开发者可以用一条明确命令证明 BOSS
半自动链路从容器启动、服务健康、队列隔离、userscript bridge 指令往返到 inspect API
失败/成功状态处理都能稳定跑通。

本任务是 `08-03-boss-communicate-testing-hardening` 的横切子任务。它不替代真实 BOSS
页面验证、dry-run 门槛或推荐列表大循环，而是为这些任务提供可重复执行的基础验收机制。

## Background

2026-08-08 对抗式审查已确认：

- `docker compose up -d --build` 可以启动 Postgres、Redis、backend、worker、frontend。
- `/api/v1/health` 返回 `db=ok`、`redis=ok`，frontend nginx 首页可访问。
- 后端在隔离测试库 `job_search_agent_audit_test` 下通过 `764 passed`，`ruff check app`
  通过。
- 前端 `pnpm lint`、`pnpm type-check`、`pnpm build` 均通过，但 build 存在大 chunk 警告。
- userscript bridge 的 heartbeat、page_id 绑定、wrong-tab requeue、result success/failure
  协议可通过 API 模拟跑通。
- `POST /api/v1/boss/recommended-jobs/current/inspect` 在没有真实 userscript result 时会超时后
  返回 `inspect_status=read_failed`，而不是无限挂起。
- 发现一处质量风险：后端测试虽然用了隔离 DB，但仍和正在运行的 Compose worker 共享默认
  Redis 队列 namespace，worker 曾消费测试任务并记录 `queue.resume_fact_extraction_missing_run`。

## Requirements

### R1. 测试数据库必须显式隔离

后端测试不得默认连接或清空本地业务库 `job_search_agent`。测试启动时必须能识别安全的测试库
配置，并在配置危险时快速失败。

### R2. 测试队列必须显式隔离

后端测试、E2E smoke 与本地业务 worker 不能共享默认 `QUEUE_NAMESPACE=job-search-agent`。所有
会触发真实 Redis/arq 的验证必须使用测试 namespace 或独立 Redis DB，并提供清理策略。

### R3. 一键 E2E smoke 覆盖核心运行面

提供一个开发者可重复执行的 smoke 入口，至少覆盖：

- Compose 服务启动和健康状态检查。
- backend `/api/v1/health`。
- frontend 入口可访问。
- worker 正在监听与 backend 相同但测试隔离的队列配置。
- userscript bridge heartbeat → next-instruction → result 往返。
- wrong `page_id` 不应窃取绑定给其它 tab 的 instruction。
- inspect API 在 userscript 未返回 JD 时有界失败并返回 `read_failed`。

### R4. Smoke 必须可审计、可清理

smoke 产生的用户、运行记录、队列 key、临时 DB 或命名空间必须有固定前缀，并且脚本退出时尽量
清理。无法安全清理的持久记录必须在输出中明确列出。

### R5. 不扩大外部副作用范围

本任务只验证本地开发链路，不登录真实 BOSS，不发送 HR 消息，不执行真实投递。真实网页和
dry-run 门槛继续由现有 P0/P3 子任务承接。

## Acceptance Criteria

- [ ] 后端测试默认配置新增安全检查：指向非测试 DB 或默认业务队列 namespace 时拒绝执行破坏性 schema 重建。
- [ ] 后端测试可在隔离 DB + 隔离 queue namespace 下通过，命令文档化。
- [ ] 新增一键 E2E smoke 入口，并在无真实 BOSS 页面时可稳定完成本地协议链路验证。
- [ ] Smoke 验证 page_id 绑定与 wrong-tab requeue，避免跨标签页抢任务。
- [ ] Smoke 验证 inspect API 的 `read_failed` 有界失败路径，包含超时上限。
- [ ] Smoke 输出关键证据：服务健康、bridge round-trip、inspect outcome、临时资源清理结果。
- [ ] `docker compose ps` 中 backend、worker、frontend、postgres、redis 都处于可用状态时，smoke 能重复运行两次且不依赖上一次残留状态。
- [ ] 质量门通过：backend `pytest`、backend `ruff`、frontend `lint`、frontend `type-check`、frontend `build`。
- [ ] 不新增真实 BOSS 登录、真实沟通、真实投递行为。

## Notes

- 主要证据文件：
  - `backend/app/tests/conftest.py`
  - `backend/app/core/config.py`
  - `backend/app/queue/runtime.py`
  - `backend/app/queue/worker.py`
  - `backend/app/api/v1/userscript_bridge.py`
  - `backend/app/api/v1/boss_recommended_jobs.py`
  - `docker-compose.yml`
- 本任务不要求解决 frontend chunk size 警告；该项作为后续性能债记录。
