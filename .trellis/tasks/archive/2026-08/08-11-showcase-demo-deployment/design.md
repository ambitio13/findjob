# Design — 展示型线上部署准备

> 技术设计文档。边界、契约、数据流、取舍与回滚。对应 PRD 的 R1–R7。

## 0. 分支与发布策略

**现状更新（2026-08-15）**：`wanzhen` 已快进合并入 `master`（两分支同指
`2a43d93`）；任务分支 `feat/showcase-demo-deployment` 已基于 `master` 创建。
仍无 git remote。原有"master 落后 75 提交"的顾虑已消除。

**改正**：

- **任务分支**：全部改动（含任务产物首提交）落在 `feat/showcase-demo-deployment`；
  验收通过后合回 `master` 并在合入点打 tag `showcase-v1`（**标签即发布物**），
  VPS 部署指向该 tag。`wanzhen` 分支可在此后清理。
- **远程仓库**：CI（R7）依赖 GitHub 仓库。由用户创建私有/公开仓库并配置
  remote 与推送权限（涉及账号凭据，agent 不代办）；在此之前 CI 工作流文件
  先行入库，推远程后自动生效。
- **部署物定义**：线上运行的代码 = `showcase-v1` tag 对应的 commit，runbook
  中记录 `git checkout showcase-v1 && docker compose ... build` 流程，保证
  "线上是什么版本"随时可回答、可复现。

## 1. 部署拓扑（R3）

```
Internet ── 443 ──▶ Caddy（自动 HTTPS，唯一对外入口）
                      ├── /api/*        ──▶ backend:8000（uvicorn，单副本）
                      ├── /api 之外     ──▶ frontend:80（nginx 静态 SPA）
                      └── (内网) backend/worker ──▶ postgres:5432 / redis:6379
```

- **选型 Caddy 做 TLS 终结**：自动申请/续期证书，配置一行域名即可，不引入
  certbot 运维负担。frontend 现有 `nginx.conf` 不改（容器内仍代理 `/api/`，
  Caddy 直连 backend 只是更短路径，二选一，采用 Caddy 直接分流）。
- 新增 `docker-compose.prod.yml`（独立文件，不覆盖 dev 编排）：
  - postgres / redis **移除 `ports`**，仅 compose 网络内可达；
  - backend / worker 环境变量走 `${VAR}` 必填校验（缺 `AUTH_SECRET_KEY`、
    `AUTH_INVITE_CODE`、`MODEL_API_KEY` 直接启动失败，靠 backend 已有的
    prod 启动校验 + compose `required` 语法）；
  - 保留容器启动 `alembic upgrade head`（已知取舍：单副本演示环境可接受；
    在 runbook 中记录）。
- 新增 `docs/showcase-deploy-runbook.md`：VPS 准备、DNS、`.env.prod` 填写、
  `docker compose -f docker-compose.prod.yml up -d`、验收清单（对齐 PRD AC）。
- **基础设施清单（R8，逐项可勾选）**：
  1. 购买 VPS（2C4G 起步，Docker 可安装）；
  2. 购买域名，添加 A 记录指向 VPS 公网 IP（验收：`dig +short <域名>`
     返回 VPS IP）；
  3. VPS 安装 Docker + compose 插件，开放 80/443，关闭其余入站端口；
  4. 首次部署后验证 Caddy 证书签发（验收：`curl -vI https://<域名>`
     证书有效，见 AC4/AC12）。
  以上均为用户操作，提前于 Phase D 完成可与开发并行。
- `.env.prod.example`：列出全部必填项与生成方式
  （`openssl rand -hex 32` 生成 `AUTH_SECRET_KEY`）。

## 2. 前端鉴权（R1）

**数据流**：`LoginPage → POST /auth/login → token 存 localStorage → axios
request interceptor 注入 Bearer → response interceptor 捕获 401 → 清 token →
跳 /login`。

- 新增 `frontend/src/features/auth/`：`LoginPage.tsx`、`useAuth` hook、
  `token.ts`（读写 localStorage 的唯一入口）。
- `client.ts` 增加两个 interceptor（不改变现有任何函数签名）。
- 路由守卫：`main.tsx` 中 `AppLayout` 外层包一个 `RequireAuth`（检查 token
  存在性；token 有效性由后端 401 兜底，前端不解析 token）。`/login` 在守卫外。
- **演示账号按钮（取舍记录）**：前端内置 `demo / <demo密码>` 一键登录。
  演示凭据故意公开——演示环境本就允许任何人体验，数据污染由重置脚本兜底。
  密码值由构建时 `VITE_DEMO_PASSWORD` 注入，避免明文出现在源码树。
- **多访客口径（更新）**：注册通道**不关闭**——`AUTH_INVITE_CODE` 保留并
  在 README 演示脚本中公布给面试官，推荐各自注册独立账号（数据互不干扰）；
  共享 demo 账号仅供快速体验，README 写明其数据互见风险与重置手段。
- 401 拦截器需防循环：仅对非 `/auth/*` 请求触发跳转。

## 3. 种子数据与重置（R2）

- 新增 `backend/scripts/seed_demo.py`（幂等）：
  1. 演示账号：经 `auth_service` 正常注册路径（username 查重保证幂等）；
  2. 3–5 条预置 JD：走 `JobPosting` 正常写入路径（`jd_raw` 自动经
     `EncryptedText` 加密，与线上行为一致），覆盖高/中/低匹配度三种画像；
  3. 示例简历：仓库内置一份**脱敏的样例 PDF**（`backend/scripts/fixtures/`），
     种子脚本走与上传相同的 `resume_storage` + facts 写入路径；
  4. match 决策 / 投递记录 / 结果事件：直接经各 repo 写入（这些是演示数据，
     不触发模型调用，避免播种成本）。
- 新增 `backend/scripts/reset_demo.py`：删除演示用户（auth_users 行）并级联
  清理其全部用户级数据（jobs / resumes / applications / actions / artifacts /
  outcomes / followups / agent_runs），随后调用 `seed_demo.seed()`。
  取舍：不做精细软删，直接硬删重建——演示环境数据无保留价值。
- 两个脚本都以 `APP_ENV` + 显式 `--i-know-this-is-demo` 参数双重防呆，
  拒绝在 `APP_ENV=prod` 且未带参数时执行（防止误伤真实部署）。

## 4. 限流适配反向代理（R4）

> **2026-08-15 更新**：本节原设计在 `RateLimitMiddleware` 中间件层实现每日
> 熔断，PRD R4 已将该口径升级为「由 08-15 子任务
> `08-15-model-gateway-budget-circuit` 在 `ModelGateway` 层实现」。以下描述
> 反映实际落地的实现。

- **起因**：uvicorn 默认不解析 `X-Forwarded-For`，`request.client.host` 是
  nginx/Caddy 的容器 IP → 所有访客共享一个限流桶。
- **改正（已由 08-15 落地）**：docker-compose backend 服务 `command:` 已加
  `--proxy-headers --forwarded-allow-ips=$${FORWARDED_ALLOW_IPS}`。
  **不用 `*`**（08-15 安全收敛：`*` 允许任意客户端伪造 XFF 绕过限流），
  而是精确指定 frontend 容器固定 IP（172.28.0.10）。`RateLimitMiddleware`
  代码不改（`request.client.host` 在开启 proxy headers 后即为真实 IP）。
- model 桶默认值从 30/分钟降到 10/分钟（配置项调整，演示场景足够），
  auth 桶维持 10/分钟。
- **每日模型预算熔断（已由 08-15 在 `ModelGateway` 层实现）**：
  - 实现层：`BudgetedModelGateway` 包装 `ModelGateway.chat`/`structured`，
    每次 LLM 调用经 Redis `INCR` 日计数（key `mb:{ns}:daily:{YYYYmmdd}`，
    48h TTL），超限当日抛 `ModelBudgetExceeded` → 429，并设
    `mb:{ns}:tripped:{YYYYmmdd}` 标志位供 health 端点读取。
  - 配置项：`MODEL_DAILY_CALL_LIMIT`（默认 500）、`MODEL_DAILY_BUDGET_ENABLED`
    （默认 true）、`MODEL_BUDGET_FAIL_OPEN`（默认 true，Redis 故障时不阻断）。
  - health 端点 `/api/v1/health` 新增 `model_budget_tripped` 字段；
    **monitoring 必须查此字段，`status` 不映射预算熔断状态**。
  - 第二道保险：runbook 要求在模型服务商侧设置 API Key 用量上限。
  - 原设计「在中间件层按 HTTP 路径分类计数」被否决——以 `ModelGateway`
    真实 LLM 调用计数更准确，不会被非模型路径污染。

## 5. BOSS 审批闭环的线上演示（R5）

- **起因**：userscript bridge 绑定 localhost + 进程内单例，线上不可用。
- **改正**：线上 BOSS 相关配置全部保持默认关闭
  （`BOSS_USERSCRIPT_BRIDGE_ENABLED=0`），adapter 注册表回落到 fake。
  演示链路：预置 JD → `POST /boss/recommended-jobs/{jobId}/match`（真实模型）
  → communicate prepare → approve → execute（fake adapter 返回 succeeded）。
- 前端适配（最小改动）：`RecommendedJobPilotPanel` 若因 bridge 未连接禁用
  了入口，则增加"演示模式"提示分支：bridge 断开且后端为 fake adapter 时
  允许走 match→execute 路径（inspect 步骤跳过，JD 来自列表预置数据）。
  若改动面超预期，降级方案：该闭环用 runbook 中的 curl 脚本演示，UI 不阻塞。
- 真实链路佐证：本地录制 userscript 实操视频 / 截图，放入 README（R6）。

## 6. 叙事页（R6）

- 改 README：新增"面试官演示路径"小节（10 分钟脚本）、架构图（mermaid）、
  三条安全不变量、测试/规约实践、BOSS 实测报告链接。不新增前端页面
  （取舍：README 是面试官更自然的入口，少写一个页面）。
- 演示脚本开头写多访客口径：优先邀请码注册独立账号；demo 账号仅快速体验，
  数据互见，可重置。

## 7. CI（R7）

- `.github/workflows/ci.yml`：
  - backend job：services 起 `postgres:16-alpine` + `redis:7-alpine`，
    env `APP_ENV=test`、`DATABASE_URL=...job_search_agent_test`、
    `QUEUE_NAMESPACE=ci-test`（满足 conftest `_guard_test_env`），
    跑 `pytest` + `ruff check`；
  - frontend job：`pnpm install --frozen-lockfile` + `lint` + `type-check`
    + `build`。

## 8. 不变量与兼容性

- 本任务**不触碰**：审批边界逻辑、安全门、加密层、sanitizer、bridge 协议。
- dev 编排（`docker-compose.yml`）与本地开发流程完全不受影响；prod 是叠加
  文件。
- 回滚：所有改动可按文件粒度撤销；部署侧回滚 = 停 prod compose、DNS 摘除。
