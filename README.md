# 求职智能助手

一个面向求职者的 AI 智能体应用：FastAPI 后端 + React + Ant Design Pro 前端 +
PostgreSQL + Redis + 模型网关（DeepSeek OpenAI 兼容）+ 轻量 Agent Runtime。
核心能力：简历上传解析 → JD 匹配决策 → JD 分析 → 审批边界护栏 → 投递漏斗。

---

## 面试官演示路径（10 分钟脚本）

> **多访客提示**：推荐用邀请码（见部署 runbook）注册独立账号体验，数据互不
> 干扰。「一键演示账号」仅供快速体验，多人同时使用会互相看到对方操作，污染后
> 可用重置脚本恢复。

### 1. 登录（1 分钟）

打开演示地址 → 登录页 → 点击「一键使用演示账号」按钮（或输入 `demo / demo12345`）。

### 2. 浏览种子数据（2 分钟）

- **职位列表**：3 条预置 JD，覆盖高/中/低三种匹配度。
- **简历**：一份脱敏示例简历 + 解析后的 facts。
- **投递记录**：2 条已投递记录 + 漏斗指标。

### 3. 匹配 → 审批闭环（4 分钟）

这是本项目最核心的安全设计——**审批边界**：

1. 选一条预置 JD → 点击「匹配分析」（真实模型调用，输出 match 决策 + 开场消息草稿）。
2. 决策为「建议沟通」→ 点击「准备沟通」（生成 `approval_required` 动作，payload 哈希锁定）。
3. 点击「审批」（人工确认，动作状态 → `approved`）。
4. 点击「执行」（fake adapter 返回 `submitted`，时间线事件完整）。

> 在线演示用 fake adapter（不依赖浏览器）。真实 userscript 链路见下方 BOSS 实测报告。

### 4. JD 粘贴解析（2 分钟）

粘贴一段 JD → 服务端 LLM 解析 → 查看结构化分析结果（技能要求 / 红旗 / readiness）。

### 5. 安全设计速览（1 分钟）

查看 `/api/v1/health`：限流状态、模型预算熔断状态、数据库/Redis 连通性。

---

## 架构图

```mermaid
graph TB
    subgraph "公网入口"
        Caddy[Caddy :443<br/>TLS 终结]
    end

    subgraph "Docker Compose appnet"
        Caddy -->|/api/*| Backend[uvicorn backend:8000<br/>FastAPI + RateLimitMiddleware]
        Caddy -->|其余| Frontend[nginx frontend:80<br/>React SPA]
        Backend --> PostgreSQL[(PostgreSQL 16)]
        Backend --> Redis[(Redis 7)]
        Worker[arq worker] --> PostgreSQL
        Worker --> Redis
        Backend --> ModelGateway[ModelGateway<br/>BudgetedModelGateway 熔断]
        Worker --> ModelGateway
        ModelGateway -->|HTTP| DeepSeek[DeepSeek API]
    end

    subgraph "安全边界"
        AuthDep[auth deps<br/>Bearer token / 401]
        RateLimit[per-IP 限流<br/>auth/model/global]
        Budget[日预算熔断<br/>Redis daily counter]
        ApprovalBoundary[审批边界<br/>prepare→approve→execute]
        ContentCrypto[内容加密<br/>enc2$ Fernet]
    end

    Backend --> AuthDep
    Backend --> RateLimit
    ModelGateway --> Budget
    Backend --> ApprovalBoundary
    Backend --> ContentCrypto
```

## 三条安全不变量

1. **审批边界**：任何改变外部状态的动作（投递、发消息）必须经过
   `prepare → approve → execute` 三阶段。prepare 生成 payload 哈希锁定的
   `approval_required` 动作；execute 验证哈希 + 审批状态后才调用 adapter。
   未审批的 execute 返回 409。测试见
   `test_boss_communicate_api.py::test_execute_unapproved_returns_409_not_approved`。

2. **安全门只降级**：模型安全门（match 决策中的 readiness gate）只能将
   `communicate` 降级为 `needs_review`，永远不会将 `skip` 升级为 `communicate`。
   降级后需人工审阅 + 确认 ack 才能继续 prepare。

3. **隐私加密**：用户长文本（如 `jd_raw`）以 `enc2$` 前缀的 Fernet 信封加密
   存储在数据库中，使用独立的 `CONTENT_ENCRYPTION_KEY`（与 `AUTH_SECRET_KEY`
   解耦）。密钥丢失则内容不可恢复。解密失败时返回空字符串——密文永不上浮为内容。

> 额外的运行时护栏：per-IP 限流（auth 10/min、model 10/min、global 300/min，
> Redis 故障 fail-open）、账号登录锁定（5 次失败 / 15 分钟，fail-open）、
> 每日模型预算熔断（超限 429，次日 UTC 午夜恢复）。

## 测试与规约实践

```bash
# 后端：全量测试 + lint
cd backend
export $(grep -v '^#' .env.test | xargs) && export QUEUE_NAMESPACE=job-search-agent-test
.venv/bin/python -m pytest app/tests -q
.venv/bin/ruff check app/

# 前端：lint + 类型检查 + 构建
cd frontend
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

- **测试隔离安全护栏**：`conftest.py` 在任何 schema 操作前校验 `APP_ENV=test`、
  `DATABASE_URL` 含 `test`、`QUEUE_NAMESPACE` 非生产默认值——防止误删业务数据库
  或污染线上 worker 队列。
- **Trellis 规约**：`.trellis/spec/` 按包/层记录编码规范，开发前通过
  `trellis-before-dev` 技能注入上下文。
- **CI**：`.github/workflows/ci.yml` 在每次推送时跑后端 pytest + ruff、
  前端 lint + type-check + build。

## BOSS 真实链路实测报告

线上演示使用 fake adapter 走通审批闭环。真实 userscript 链路（通过 Tampermonkey
油猴脚本在 BOSS 直聘页面内执行 inspect + execute）的本地实测报告与录屏：

- [BOSS 本地开发 Runbook](docs/boss-local-dev-runbook.md)
- [BOSS 对抗性自动化发现报告](docs/boss-anti-automation-findings.md)
- [BOSS 沟通测试计划](docs/boss-communicate-testing-plan.md)
- [BOSS 手动 Pilot 实测记录](docs/manual-boss-pilot.md)
- [E2E Smoke 测试](docs/e2e-smoke.md)

## 目录结构

```text
backend/                  FastAPI 后端
  app/
    api/v1/               路由层（transport）
    services/             领域服务（domain）
    db/                   ORM 模型 + 仓库（persistence）
    models_gateway/       模型网关（DeepSeek / Fake / 预算熔断）
    platforms/boss/       BOSS 平台适配器（fake / real / userscript bridge）
    core/                 配置 / 安全 / 加密 / 日志
    queue/                arq worker + handlers
  scripts/
    seed_demo.py          演示数据播种（幂等）
    reset_demo.py         演示数据重置（硬删 + 重新播种）
  alembic/                数据库迁移
frontend/                 React + Ant Design Pro 前端
  src/
    features/auth/        鉴权（token / useAuth / RequireAuth / LoginPage）
    features/applications/ 投递 + BOSS pilot 面板
    api/client.ts         axios 客户端（interceptors 注入 Bearer）
    pages/                页面组件
docker-compose.yml        开发编排
docker-compose.prod.yml   生产编排（postgres/redis 无端口、Caddy TLS）
Caddyfile                 Caddy 反向代理模板
.env.example              开发环境变量样例
.env.prod.example         生产环境变量样例（含密钥生成命令）
docs/
  showcase-deploy-runbook.md  部署 + 验收手册（对齐 AC1–AC12）
  boss-*.md                BOSS 链路实测文档
.trellis/                 Trellis 任务管理 + 编码规范
```

## 快速开始

### 方式一：Docker Compose（开发环境）

```bash
cp .env.example .env          # 按需调整，默认无需模型 Key
docker compose up --build
```

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000/api/v1/health

### 方式二：本地直接运行

```bash
# 后端
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head           # 需要本地 PostgreSQL
uvicorn app.main:app --reload --port 8000

# 队列 Worker（另开终端）
cd backend && . .venv/bin/activate
arq app.queue.worker.WorkerSettings

# 前端（另开终端）
cd frontend
pnpm install
pnpm dev                       # http://localhost:5173
```

## 生产部署

详见 [docs/showcase-deploy-runbook.md](docs/showcase-deploy-runbook.md)。

```bash
cp .env.prod.example .env.prod   # 填入真实密钥（含生成命令）
vim Caddyfile                     # 替换 SHOWCASE_DOMAIN 为你的域名
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
    python scripts/seed_demo.py --i-know-this-is-demo
```

## 环境变量

根目录 `.env.example`（开发）与 `.env.prod.example`（生产）列出全部变量。
关键项：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_ENV` | `local` / `test` / `prod` | `local` |
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg://app:app@localhost:5432/job_search_agent` |
| `REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |
| `MODEL_API_KEY` | 模型 API Key，留空则自动用 Fake 提供者 | 空 |
| `AUTH_SECRET_KEY` | token 签名密钥（prod 必填） | 空 |
| `AUTH_INVITE_CODE` | 注册邀请码（prod 必填） | 空 |
| `CONTENT_ENCRYPTION_KEY` | Fernet 内容加密密钥（prod 必填） | 空 |
| `FORWARDED_ALLOW_IPS` | 受信反向代理 IP（**禁止 `*`**） | `127.0.0.1` |
| `MODEL_DAILY_CALL_LIMIT` | 每日模型调用上限 | `500` |

> `AUTH_SECRET_KEY` / `MODEL_API_KEY` / `AUTH_INVITE_CODE` / `CONTENT_ENCRYPTION_KEY`
> 绝不入库、不进前端产物。

## 相关文档

- 部署手册：[docs/showcase-deploy-runbook.md](docs/showcase-deploy-runbook.md)
- BOSS 本地开发：[docs/boss-local-dev-runbook.md](docs/boss-local-dev-runbook.md)
- 任务管理：`.trellis/tasks/`
- 编码规范：`.trellis/spec/`
