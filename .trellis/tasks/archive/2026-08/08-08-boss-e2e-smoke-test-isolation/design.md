# BOSS E2E smoke 与测试隔离质量门 Design

## Architecture

本任务分成两个边界清晰的交付面：

1. 测试安全边界：后端测试 fixture 在执行 `drop_all/create_all` 前确认当前 DB 和队列配置是测试环境。
2. 本地 smoke 边界：独立脚本编排 Docker Compose、HTTP API、userscript bridge 模拟客户端和资源清理。

不引入新的队列框架。现有 `QUEUE_NAMESPACE` 已经由 `backend/app/queue/runtime.py` 和
`backend/app/queue/worker.py` 使用，设计上只需要让测试和 smoke 使用独立 namespace，并在危险配置下失败。

## Configuration Contract

### Backend tests

测试进程必须显式设置：

- `APP_ENV=test`
- `MODEL_PROVIDER=fake`
- `RESUME_UPLOAD_DIR=<temp dir>`
- `DATABASE_URL` 指向名称包含 `_test`、`test_` 或专用 allowlist 的数据库
- `QUEUE_NAMESPACE` 不等于 `job-search-agent`

如果测试需要真实 Redis/arq，使用 `QUEUE_NAMESPACE=job-search-agent-test-<run-id>` 或独立 Redis DB。
只使用 fake queue 的单元测试仍需避免默认 namespace，防止代码路径变化后静默污染本地 worker。

### E2E smoke

smoke 可以采用下列两种实现之一，优先级从高到低：

- 推荐：`scripts/e2e-smoke.sh` 负责创建临时 DB、生成测试 namespace、运行 API 验证、清理资源。
- 可选：Python helper 承担 JSON 解析和轮询逻辑，shell 脚本只做编排。

脚本输入建议：

- `BACKEND_BASE_URL`，默认 `http://localhost:8000/api/v1`
- `FRONTEND_BASE_URL`，默认 `http://localhost:5173`
- `SMOKE_DB_NAME`，默认 `job_search_agent_smoke_<timestamp>`
- `SMOKE_QUEUE_NAMESPACE`，默认 `job-search-agent-smoke-<timestamp>`
- `SMOKE_USER_ID`，默认 `smoke-review-user-<timestamp>`

## Data Flow

### Service health path

`docker compose ps` → backend `/health` → frontend `/` → worker log/readiness check。

验收重点是确认服务启动完成且 backend 连接的 DB/Redis 可用。worker readiness 不能只看容器
running，至少要确认 worker 注册函数启动日志或 arq queue 可被访问。

### Bridge protocol path

1. Smoke POST `/userscript-bridge/heartbeat`，携带固定 `page_id` 和 `page_url_hash`。
2. Smoke 触发 `/userscript-bridge/probe` 或其它测试 instruction。
3. Smoke 用错误 `page_id` 拉取 `/next-instruction`，期望 `204`。
4. Smoke 用正确 `page_id` 拉取 instruction，校验 `instruction_id`、`page_id`、
   `expected_url_hash`。
5. Smoke POST `/userscript-bridge/result` 回传成功和失败结果。
6. Smoke 校验 probe response 或后端 API response。

### Inspect bounded-failure path

1. Smoke 以 `X-User-Id: <SMOKE_USER_ID>` 调用 `/boss/recommended-jobs/current/inspect`。
2. 在没有真实 userscript JD result 的前提下，API 必须在配置的等待上限内返回。
3. 期望 `inspect_status=read_failed`，`job=null`，`application=null`。
4. 输出 `agent_run_id`，并清理或记录不可清理的持久数据。

## Persistence and Cleanup

测试数据库可以在脚本退出时 drop。业务库中的 smoke 用户和 run 只允许在明确解析出
`SMOKE_USER_ID` 前缀且相关记录范围可枚举时清理；否则输出残留记录 ID，交给开发者确认。

Redis 清理必须限定到测试 namespace 前缀，不允许 flushdb。

## Compatibility

- 不改变生产默认 `QUEUE_NAMESPACE=job-search-agent`。
- 不改变现有 API response contract。
- 不要求真实 BOSS 页面在线。
- 不要求新增前端 UI。
- 允许新增 scripts、测试 fixture 安全检查、测试辅助模块和文档。

## Risks

- 如果 smoke 在业务 Compose worker 运行时使用默认 namespace，仍可能被 worker 抢任务；实现必须先解决隔离再扩展 smoke。
- 如果 inspect 默认等待时间过长，smoke 会变慢；可以为本地 smoke 增加安全的测试配置项，但不能降低生产默认可靠性。
- 如果清理逻辑过宽，会误删开发数据；清理必须使用固定前缀和显式目标。

## Rollback

本任务应尽量只增加安全检查、脚本和测试。如果出现回归，回滚范围限定为：

- 新增 smoke 脚本或 helper。
- 测试 fixture 中新增的配置 guard。
- 新增测试用例。

不得回滚已有 BOSS bridge、selector、frontend pilot panel 的业务改动。
