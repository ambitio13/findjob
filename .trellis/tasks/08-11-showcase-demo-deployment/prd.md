# 展示型线上部署准备：面试官可访问的 Demo 环境

## Background（起因）

本项目定位调整为**面试展示用途**：部署到公网供面试官体验，不开放给真实用户使用。
2026-08-11 对抗性评审发现以下问题导致当前代码**无法直接用于展示型部署**：

1. **前端完全没有接鉴权**（起因）：后端已有完整的注册/登录/token 体系
   （`/api/v1/auth/*`），但前端没有登录页、`client.ts` 从不附加 `Authorization` 头。
   `APP_ENV=prod` 时所有 API 返回 401，前端 100% 不可用；不切 prod 则任何人以
   `demo_user` 身份读写全部数据。（改正方向见 R1）
2. **没有可展示的部署形态**（起因）：仓库只有 dev 用 docker-compose，Postgres/Redis
   端口直接暴露宿主机、默认弱口令 `app/app`、无 TLS、无生产环境变量样例。
   （改正方向见 R3）
3. **空数据无法演示**（起因）：新环境登录后是空的职位/简历/投递列表，面试官
   看不到产品价值；且访客操作可能污染数据，需要快速恢复手段。（改正方向见 R2）
4. **限流在反向代理后失效**（起因）：`RateLimitMiddleware` 用 `request.client.host`，
   而 uvicorn 未开 `--proxy-headers`，部署后所有访客共享 nginx 容器 IP 这一个桶，
   公网暴露的真实 `MODEL_API_KEY` 存在被刷爆费用的风险。（改正方向见 R4）
5. **BOSS userscript 链路无法远程演示**（起因）：userscript `@connect` 仅限
   localhost，bridge 通道是进程内单例，线上无法复现 inspect→execute 流程。
   （改正方向见 R5：线上不抓 JD，用预置 JD + fake adapter 走通审批闭环）

**演示主线确认**（与需求方对齐）：
- 线上**不做** JD 抓取（userscript/CDP 路线只留在本地与录屏中）；
- 简历上传 + 服务端解析**保留**，线上可用；
- 主线为：上传简历 → 匹配**预置 JD**（match 决策 / JD 分析 / readiness）→
  审批边界流程演示（fake adapter）→ 漏斗指标；
- JD 粘贴解析（纯后端 LLM 流程，无浏览器依赖）保留为可交互演示点。

## Goal

让项目以 `APP_ENV=prod` 部署到一个公网 HTTPS 地址，面试官可以用演示账号登录，
在 10 分钟内体验完整的"简历 → 匹配 → 分析 → 审批护栏"主线；公网暴露面收敛到
最小，模型费用有确定性上限保护。

## Requirements

### R1 前端接入鉴权（阻断级）
- 新增登录页（用户名/密码），登录成功后持久化 token 并在所有 API 请求注入
  `Authorization: Bearer` 头；401 时跳回登录页。
- 登录页提供"一键使用演示账号"按钮（演示凭据写在前端是演示场景的可接受取舍，
  需在 design.md 记录）。
- 线上关闭公开注册：部署环境必须配置 `AUTH_INVITE_CODE`。

### R2 种子数据与一键重置（阻断级）
- 提供幂等的种子脚本：演示账号、3–5 条预置 JD（覆盖不同匹配度）、一份示例简历
  事实集、match 决策/投递记录/漏斗指标样例，保证登录后首屏有内容。
- 提供一键重置脚本（清空演示用户数据并重新播种），30 秒内可恢复被污染的环境。

### R3 部署安全基线（阻断级）
- 提供生产部署编排（compose prod 变体 + `.env.prod.example`）：
  `APP_ENV=prod`、强随机 `AUTH_SECRET_KEY`、真实 `MODEL_API_KEY`、`AUTH_INVITE_CODE`；
- Postgres / Redis 不再向宿主机暴露端口；仅 80/443 对外；
- 域名 + HTTPS（反向代理终结 TLS）；
- 容器启动自动 `alembic upgrade head` 的行为在 prod 编排中保留但记录为已知取舍
  （单副本演示环境可接受）。

### R4 限流适配反向代理 + 每日模型预算熔断（高风险）
- uvicorn 启用 proxy headers，使限流拿到访客真实 IP；
- 验证 auth / model / global 三桶在代理后按真实 IP 生效；
- 调低 model 桶上限（30→10/分钟）；
- **每日模型预算熔断（升级为必做）**：由子任务 `08-15-model-gateway-budget-circuit`
  取代原"model 桶命中时 Redis 按日计数"的实现口径——熔断以 `ModelGateway` 真实
  LLM 调用计数（`chat` + `structured`），不再以 HTTP 路径分类为准。超过可配置
  日上限后当日返回 429；runbook 同时要求在模型服务商侧设置 API Key 用量上限
  作为第二道保险。分钟限流挡不住长时窗持续刷量，公网真实 Key 必须有日级费用
  确定性。

### R5 BOSS 审批闭环的线上可演示形态（展示效果）
- 验证默认 fake adapter 下 inspect→match→prepare→approve→execute 全链路可走通
  （execute 返回 fake 成功结果，不依赖浏览器）；
- 前端/文档准备真实 userscript 链路的录屏或截图（本地实拍），作为真实性佐证。

### R6 项目叙事页（展示效果）
- README 或前端“关于”页承载：一张架构图、三条安全不变量（审批边界 / 只降级的
  安全门 / 隐私加密）、测试与规约实践说明，供面试官 30 秒抓住深度。
- **多访客演示口径**：README 演示脚本写明——面试官推荐用邀请码注册独立账号
  （数据互不干扰）；「一键演示账号」仅供快速体验，多人同时使用可能互相看到
  对方操作，污染后可用重置脚本恢复。

### R7 最小 CI（工程信号）
- 一条流水线：backend `pytest` + `ruff`；frontend `lint` + `type-check` + `build`。

### R8 部署基础设施落地（阻断级，用户侧操作项显式化）
- VPS（2C4G 起步即可）与域名购买、DNS A 记录指向 VPS ——用户操作，需在
  runbook 中给出逐步清单与验收方式（dig / curl 检查）；
- Caddy 自动 TLS 证书签发验证（首次部署后证书有效即为验收）；
- 上述基础设施就绪是 Phase G 部署演练的硬前置，提前于 Phase D 完成可并行。

## Explicitly Out of Scope（明确不做）

- userscript/bridge 远程化、多副本水平扩展；
- token refresh / 登出吊销体系、密码找回；
- 简历病毒扫描、密钥轮换、数据库备份策略；
- BOSS 真实浏览器链路的公网化（合规与架构上均不成立）。

## Acceptance Criteria

- [ ] AC1：全新环境按部署文档拉起后，浏览器打开域名可见登录页；用演示账号
      登录成功，职位/简历/投递/指标页均有种子数据。
- [ ] AC2：未登录访问任何业务页面被重定向到登录页；直接调 API 返回 401。
- [ ] AC3：`AUTH_INVITE_CODE` 生效，无邀请码注册被拒绝（400）。
- [ ] AC4：宿主机 `nmap`/端口检查仅见 80/443；HTTPS 证书有效。
- [ ] AC5：从两个不同公网 IP 分别压测 model 桶接口，各自独立触发 429（证明
      限流按真实 IP 生效）。
- [ ] AC6：fake adapter 下完整走通一次 match→prepare→approve→execute，
      execute 返回 submitted，时间线事件完整。
- [ ] AC7：重置脚本执行后，环境回到种子态（AC1 复验通过）。
- [ ] AC8：CI 流水线在合入前绿（后端测试 + lint、前端 lint + 类型检查 + 构建）。
- [ ] AC9：README/关于页包含架构图、安全不变量、演示脚本（给面试官的操作路径）。
- [ ] AC10：线上部署物可追溯——运行版本对应明确的 git tag（`showcase-v1`），
      本任务全部改动在独立 feature 分支完成并合回。
- [ ] AC11：每日模型熔断生效——测试环境将日上限设为 N 后第 N+1 次 LLM 调用
      （通过任意触发 `ModelGateway.chat`/`structured` 的端点验证，不再限定 model
      桶 HTTP 路径）返回 429，且次日（或 Redis key 过期后）自动恢复；
      `/api/v1/health` 的 `model_budget_tripped` 字段在熔断后为 true；
      runbook 含服务商侧用量上限设置步骤。
- [ ] AC12：基础设施就绪——域名解析指向 VPS（dig 可验）、Caddy 证书有效、
      runbook 基础设施清单一项可勾选完成。

## Constraints

- 不得削弱既有安全不变量（审批边界、安全门只降级、隐私加密、404 隔离）。
- 全部现有测试（956 个）保持通过；新增行为补测试。
- 演示凭据属于故意公开的信息，但 `AUTH_SECRET_KEY` / `MODEL_API_KEY` /
  `AUTH_INVITE_CODE` 绝不入库、不进前端产物。
