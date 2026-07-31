# 求职智能助手 · MVP 项目骨架

一个面向求职者的 AI 智能体应用骨架。本仓库只包含 MVP 基础结构：FastAPI 后端、
React + Ant Design Pro 前端、PostgreSQL、Redis、模型网关（DeepSeek OpenAI 兼容）、
以及轻量 Agent Runtime。

## 范围

**包含（MVP 骨架）**

- 手动录入 / 导入 JD
- 简历上传与解析基础结构
- JD 分析基础结构
- 简历改写片段生成占位
- HR 开场消息生成占位
- 技能缺口 / 面试准备输出占位
- 模型网关抽象 + DeepSeek + Fake 提供者
- 轻量 Agent Runtime（planner / executor / reflector / tools / memory）

**不包含（已明确排除）**

- 招聘平台自动化、自动投递、自动发送 HR 消息
- 浏览器自动化
- 完整 Word / PDF 定制简历导出
- 复杂多智能体规划与生产级 RAG

## 目录结构

```text
backend/      FastAPI 后端（配置、DB、Redis、模型网关、Agent Runtime）
frontend/     React + Ant Design Pro 前端（职位列表、JD 录入、详情、健康状态）
docker-compose.yml   PostgreSQL + Redis + 后端 + 前端
.trellis/     Trellis 任务与规范
```

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env          # 按需调整，默认无需模型 Key
docker compose up --build
```

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000/api/v1/health

后端容器启动时会自动执行 `alembic upgrade head` 建表。

### 方式二：本地直接运行

后端：

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env           # 本地连接指向 localhost
alembic upgrade head           # 需要本地 PostgreSQL
uvicorn app.main:app --reload --port 8000
```

队列 Worker（另开终端，消费模型任务）：

```bash
cd backend && . .venv/bin/activate
arq app.queue.worker.WorkerSettings
```

前端：

```bash
cd frontend
pnpm install
pnpm dev                       # http://localhost:5173，已代理 /api 到 8000
```

## 环境变量

根目录 `.env.example` 与 `backend/.env.example` 列出全部变量。关键项：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg://app:app@localhost:5432/job_search_agent` |
| `REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |
| `QUEUE_NAMESPACE` | arq 队列命名空间（多环境共享 Redis 时隔离） | `job-search-agent` |
| `QUEUE_JOB_TIMEOUT` | 单个队列任务执行超时（秒） | `300` |
| `QUEUE_MAX_RETRIES` | 任务失败重试次数 | `2` |
| `MODEL_PROVIDER` | `auto` / `fake` / `deepseek` / `openai` | `auto` |
| `MODEL_API_KEY` | 模型 API Key，留空则自动用 Fake 提供者 | 空 |
| `MODEL_BASE_URL` | OpenAI 兼容 Base URL | `https://api.deepseek.com` |
| `MODEL_DEFAULT_MODEL` | 默认模型名 | `deepseek-chat` |

> 没有模型 Key 时本地测试照常通过（使用 Fake 提供者，不发起网络请求）。

## 开发与校验命令

```bash
# 后端
cd backend && pytest
cd backend && ruff check .

# 前端
cd frontend && pnpm lint
cd frontend && pnpm type-check
cd frontend && pnpm build

# 全栈
docker compose up --build
```

## 架构要点

- **后端分层**：路由（transport）→ 服务（domain）→ 仓库（persistence）。路由不
  直接调用模型 SDK 或写复杂 SQL。
- **模型网关**：所有模型访问必须经 `app.models_gateway`。DeepSeek 提供者是唯一
  允许发起 HTTP 模型调用的模块。业务代码只依赖 `ModelGateway` 接口。
- **Agent Runtime**：以 `AgentRun` / `AgentStep` / `ToolCall` / `Artifact` 为持久
  概念，planner / executor / reflector / tools / memory 可替换。当前提供一个
  确定性的 `manual_jd_analysis_demo` 冒烟流程。
- **数据模型**：PostgreSQL 为唯一持久化真值；Redis 仅用于缓存 / 锁 / 队列 / 热会话。
- **用户控制**：任何改变外部状态的动作（投递、发消息）必须有显式授权与审计——MVP
  暂不实现这些动作，但骨架已为其预留接口。

## 相关文档

- 需求 / 设计 / 执行计划：`.trellis/tasks/07-31-mvp-project-skeleton/`
- 编码规范：`.trellis/spec/`
- 项目立项：`项目立项/`
