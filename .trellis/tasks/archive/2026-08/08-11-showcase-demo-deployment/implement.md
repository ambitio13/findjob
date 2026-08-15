# Implement — 展示型线上部署准备

> 执行计划。按阶段顺序推进；每阶段末尾有验证命令，全部通过才进入下一阶段。
> 前置阅读：`prd.md`（起因与验收标准）、`design.md`（技术取舍）。

## Phase 0：分支准备（design.md §0）

- [x] P0-1. 任务分支已就绪：`feat/showcase-demo-deployment`（基于已合并的
      `master`，非原计划的 `wanzhen`；两分支同指 `2a43d93`，见 design.md §0 现状更新）
- [ ] P0-2. 任务产物（`.trellis/tasks/08-11-showcase-demo-deployment/`）作为
      该分支首提交（需用户确认后提交）
- [ ] P0-3. 提醒用户创建 GitHub 仓库并配 remote（用户操作，不阻塞 A–E，
      阻塞 F 的 CI 生效与最终推送）
- [ ] P0-4. 提醒用户启动 R8 基础设施清单（VPS/域名/DNS，用户操作，
      不阻塞 A–F，阻塞 G 的部署演练；提前完成可与开发并行）

## Phase A：后端限流与代理修复（PRD R4）

> **2026-08-15 更新**：A1/A4/A5 已由 08-15 子任务
> `08-15-model-gateway-budget-circuit`、`08-15-proxy-real-ip-trust-chain`
> 落地。A2 在本阶段完成。R4 日预算熔断验收口径已由 08-15 实现取代。

- [x] A1. docker-compose backend `command:` 已加 `--proxy-headers
      --forwarded-allow-ips`（08-15 落地，用精确 IP 非 `*`）
- [x] A2. `app/core/config.py` 将 `rate_limit_model_per_minute` 默认值 30 → 10
- [x] A3. `classify_bucket` 回归 + model 桶新默认值断言（08-15 已覆盖回归）
- [x] A4. 每日模型熔断：`BudgetedModelGateway` 包装 `ModelGateway`，
      Redis 日计数 `mb:{ns}:daily:{YYYYmmdd}`，超限 429，
      响应体标明 budget_exceeded（08-15 落地，详见 design.md §4 更新）
- [x] A5. 熔断测试：日上限 N 时第 N+1 次 LLM 调用 429；`/api/v1/health`
      `model_budget_tripped` 为 true；次日/Redis key 过期后自动恢复
      （08-15 落地，测试见 `test_model_budget.py`）
- [x] A6. `.env.prod.example` / runbook 补充服务商侧 API Key 用量上限设置步骤
      （Phase D 一并完成，见 `docs/showcase-deploy-runbook.md` §5）

验证：
```bash
cd backend && source .env.test && export QUEUE_NAMESPACE=job-search-agent-test
pytest -q && ruff check .
```

回滚点：A 阶段独立提交，可单独 revert。

## Phase B：前端鉴权（PRD R1）

- [x] B1. `frontend/src/features/auth/token.ts`：localStorage token 读写唯一入口
- [x] B2. `client.ts`：request interceptor 注入 Bearer；response interceptor
      对非 `/auth/*` 的 401 清 token 并跳 `/login`（防循环）
- [x] B3. `LoginPage.tsx` + `useAuth`（POST `/auth/login`）；含"一键演示账号"
      按钮（凭据经 `VITE_DEMO_PASSWORD` 构建注入）
- [x] B4. `main.tsx`：`RequireAuth` 守卫包住 `AppLayout`，`/login` 在守卫外
- [x] B5. `AppLayout` 显示当前用户名 + "退出"（清 token 跳登录页）

验证：
```bash
cd frontend && pnpm lint && pnpm type-check && pnpm build
# 本地联调：dev compose 起后端（APP_ENV=local 无需 token 也可回归旧行为），
# 手动验证登录/退出/刷新保持登录/无 token 直调 API 被拒
```

注意：`APP_ENV=local` 的 demo_user 回退行为不变（后端既有逻辑），本地开发
体验不受影响；prod 行为由后端 401 保证，无需前端分支。

## Phase C：种子数据与重置（PRD R2）

- [x] C1. 制作脱敏样例简历 → `backend/scripts/fixtures/sample_resume.txt`
      （用文本 fixture 而非 PDF，种子脚本直接写入 facts，无需 PDF 解析）
- [x] C2. `backend/scripts/seed_demo.py`：演示账号 / 3 条预置 JD（高/中/低
      匹配度）/ 简历+facts / match 决策 / 投递记录 / 结果事件；全程走现有
      service/repo 写入路径，幂等
- [x] C3. `backend/scripts/reset_demo.py`：硬删演示用户级联数据 → 重新播种；
      `--i-know-this-is-demo` 防呆
- [x] C4. 新增测试：seed 幂等性（跑两次数据不重复）、reset 后计数归零再播种
      （`app/tests/test_seed_demo.py`，5 个测试全绿）

验证：
```bash
cd backend && pytest app/tests/test_seed_demo.py -q
# 本地 compose 环境实跑：seed → 页面可见数据 → reset → 再次 seed
```

## Phase D：生产部署编排（PRD R3）

- [x] D1. `docker-compose.prod.yml`：postgres/redis 无 ports 暴露；backend /
      worker / frontend / caddy 四个服务；必填环境变量缺失即启动失败
      （`${VAR:?required}` 语法）
- [x] D2. `Caddyfile`（仓库内模板）：域名 → TLS 自动证书，`/api/*` →
      backend:8000，其余 → frontend:80
- [x] D3. `.env.prod.example`：全部必填项 + 注释（含 `openssl rand -hex 32`
      生成 `AUTH_SECRET_KEY`、Fernet 密钥生成命令、`AUTH_INVITE_CODE`、
      `VITE_DEMO_PASSWORD`、`FORWARDED_ALLOW_IPS=172.28.0.10` 禁止 `*`、
      全部 08-15 新增配置项）
- [x] D4. `docs/showcase-deploy-runbook.md`：VPS/DNS/部署/验收清单
      （逐条对齐 PRD AC1–AC12，含 R8 基础设施逐项勾选清单与 dig/curl 验收
      命令）+ 模型 Key 用量上限设置指引 + 已知取舍记录

验证：
```bash
# 本机模拟 prod：APP_ENV=prod + 假密钥拉起 prod compose，
# 验证 401、邀请码拒绝注册、端口仅 80/443
docker compose -f docker-compose.prod.yml config --quiet
```

## Phase E：BOSS fake 演示链路（PRD R5）

- [x] E1. 实测 fake adapter 下 match → prepare → approve → execute 全链路
      （现有测试 `test_boss_communicate_api.py::test_execute_approved_returns_200_with_terminal_result`
      覆盖 match→prepare→approve→execute，execute 返回 submitted；fake adapter
      默认在 registry 中启用，无需 bridge）
- [x] E2. 前端 `RecommendedJobPilotPanel` 演示模式适配：当 application 已有
      job_id（种子数据）时跳过 inspect 步骤直接从 match 开始，inspect 卡片
      在此条件下隐藏。match/prepare/approve/execute 步骤不依赖 bridge 状态
- [ ] E3. 本地录制真实 userscript 链路视频/截图 → 存入 `docs/`（不入库大文件
      则放外链并在 README 引用）— 用户侧操作

## Phase F：叙事与 CI（PRD R6 + R7）

- [x] F1. README 增补：面试官 10 分钟演示脚本（开头写多访客口径：优先邀请码
      注册独立账号，demo 账号仅快速体验且数据互见可重置）、mermaid 架构图、
      三条安全不变量、测试/规约实践、BOSS 实测报告与录屏链接
- [x] F2. `.github/workflows/ci.yml`：backend（postgres+redis services，
      `APP_ENV=test`、`QUEUE_NAMESPACE=ci-test`，pytest + ruff）；
      frontend（lint + type-check + build）；compose config 校验（dev + prod）
- [ ] F3. 推远程仓库，确认 CI 跑绿（依赖 P0-3 用户完成 remote 配置）

## Phase G：部署演练与验收

- [ ] G1. 按 runbook 部署到 VPS，逐条过 PRD AC1–AC12（含 AC11 日熔断线上
      抽验、AC12 基础设施清单勾选）
- [ ] G2. AC5 双 IP 限流验证、AC6 fake 闭环验证、AC7 重置验证
- [ ] G3. 邀请一位非项目成员走一遍"面试官路径"，记录卡点并修
- [ ] G4. 验收全过后：feature 分支合回 `master`，在合入点打 tag
      `showcase-v1`；runbook 记录线上部署物即该 tag

## Review Gates

1. Phase B 完成后：本地手动回归登录闭环（进入 C 前）。
2. Phase D 完成后：本机 prod 模拟演练（进入 E 前）。
3. Phase F 完成后：CI 全绿 + trellis-check（进入部署演练前）。

## 全局验证命令

```bash
cd backend  && pytest -q && ruff check .
cd frontend && pnpm lint && pnpm type-check && pnpm build
```
