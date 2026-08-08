# BOSS E2E smoke 与测试隔离质量门 Implementation Plan

## Ordered Checklist

- [ ] 读取实现前规范：shared `code-quality.md`、`dependencies.md`；backend `quality.md`、
  `database.md`、`performance.md`、`logging.md`、`api-contracts.md`、`authentication.md`；
  frontend `quality.md`、`api-integration.md`。
- [ ] 在 `backend/app/tests/conftest.py` 增加测试配置 guard：
  - `APP_ENV` 必须是 `test`。
  - `DATABASE_URL` 必须显式指向测试库。
  - `QUEUE_NAMESPACE` 必须显式测试化且不能是 `job-search-agent`。
  - guard 在 `Base.metadata.drop_all()` 前执行。
- [ ] 补充后端测试，覆盖危险 DB/queue 配置会快速失败，安全测试配置继续通过。
- [ ] 设计 smoke 入口，优先新增 `scripts/e2e-smoke.sh`；必要时新增小型 Python helper 处理 JSON 和轮询。
- [ ] smoke 启动/检查 Compose 服务：
  - `docker compose up -d --build`
  - `docker compose ps`
  - backend `/api/v1/health`
  - frontend `/`
  - worker readiness 证据
- [ ] smoke 使用隔离 DB 和隔离 `QUEUE_NAMESPACE`，并在输出中打印实际使用的 DB/namespace。
- [ ] smoke 跑 bridge 协议：
  - heartbeat
  - probe
  - wrong-tab `204`
  - correct-tab instruction
  - result success
  - result failure/error path
- [ ] smoke 跑 inspect bounded-failure：
  - `POST /api/v1/boss/recommended-jobs/current/inspect`
  - 期望 `inspect_status=read_failed`
  - 输出并尽量清理 `SMOKE_USER_ID` 相关持久记录
- [ ] 增加文档说明：如何运行 smoke、如何解释失败、如何清理残留。
- [ ] 更新父任务 PRD，标注该任务是横切质量门，不计入原 9 项业务路线图但阻塞后续大规模自动化。

## Validation Commands

后端测试使用隔离 DB 和隔离 queue namespace：

```bash
cd backend
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/job_search_agent_test \
QUEUE_NAMESPACE=job-search-agent-test \
./.venv/bin/pytest -q
```

后端 lint：

```bash
cd backend
./.venv/bin/ruff check app
```

前端质量门：

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

全链路 smoke：

```bash
./scripts/e2e-smoke.sh
```

补充检查：

```bash
git diff --check
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
```

## Review Gates

- 开发前：必须确认本任务仍处于 `planning`，并在用户批准后运行 `task.py start`。
- 第一个 gate：危险测试配置 guard 已覆盖，且不会误伤明确的测试 DB。
- 第二个 gate：smoke 连续运行两次通过，不依赖上一次残留。
- 第三个 gate：worker 日志没有 `missing_run`、跨 namespace 消费或未解释 traceback。
- 最终 gate：所有质量命令通过；若 frontend build 仅有 chunk warning，可记录为非阻塞性能债。

## Rollback Points

- 如果测试 guard 误判，先回滚 guard 测试和规则，不动业务代码。
- 如果 smoke 脚本不稳定，保留后端隔离 guard，回滚脚本实现到上一个可运行版本。
- 如果 Compose 环境无法共享同一隔离 namespace，需要将 smoke 拆成 `docker compose` override 文件；不改生产默认 compose。

## Out of Scope During Implementation

- 不实现真实 BOSS 登录。
- 不发送真实 HR 沟通消息。
- 不启用推荐列表 auto-execute。
- 不处理 frontend chunk split，除非它阻塞 smoke。
