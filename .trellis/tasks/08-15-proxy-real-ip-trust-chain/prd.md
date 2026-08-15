# 反代真实 IP 信任链与限流防伪造

## 依赖声明

- **Depends on**: 无（Phase 1，可与其他 Phase 1 子任务并行）。
- **Blocks**: `08-15-runtime-guardrails`（账号锁定的伪造 IP 绕过验收依赖本任务）；
  `08-11-showcase-demo-deployment` R4 的"uvicorn 启用 proxy headers"半成品规划
  与 AC5（AC5 现有写法测不出伪造绕过，需强化）。

## Background（漏洞 A2）

`RateLimitMiddleware` 用 `request.client.host` 做 per-IP 计数。两层问题：

1. **uvicorn `--proxy-headers` 默认只信任 `127.0.0.1`**（`--forwarded-allow-ips`
   默认值）。docker compose 网络里 nginx 容器 IP 不是 127.0.0.1，直接加
   `--proxy-headers` 不生效，限流仍拿到 nginx IP——所有访客共享一个桶。
2. **若为了让它生效把信任设为 `*`**：攻击者自带随机 `X-Forwarded-For` 头，
   每次请求一个"新 IP"，**彻底绕过所有 per-IP 限流**（auth 桶爆破保护、model
   桶、global 桶全部失效）。

`08-11` R4 只写了"启用 proxy headers"，未定义信任 IP 策略，两条坑都没覆盖。

## Goal

限流拿到的客户端 IP 是真实访客 IP，且客户端无法通过伪造 `X-Forwarded-For` /
`X-Real-IP` 头污染它。

## Requirements

- R1 uvicorn 启动参数（backend Dockerfile CMD / compose command）启用
  `--proxy-headers`，并将 `FORWARDED_ALLOW_IPS` 配置为**明确的 nginx 容器来源**
  （推荐 compose 服务名解析出的网段或固定静态 IP），绝不使用 `*`。
- R2 nginx 侧确认 `X-Forwarded-For` 使用 `$proxy_add_x_forwarded_for`（追加而非
  覆盖），且 `X-Real-IP` 由 nginx 设置、后端忽略客户端自带的同名头。
- R3 信任 IP 列表来自环境变量（如 `FORWARDED_ALLOW_IPS`），在 `.env.prod.example`
  中给出示例与"为什么不能是 *"的注释；本地 dev（无反代直连 8000）行为不变。
- R4 新增/更新测试：带伪造 `X-Forwarded-For` 的直连请求（不经 nginx）不会改变
  `request.client.host`（uvicorn 未信任该来源时忽略头）。
- R5 回写 `08-11` prd：R4 文案与 AC5 补充"伪造 XFF 不能改变限流身份"的验收项
  （在本任务完成时执行，或由父任务收口统一执行，二选一需在本任务 implement 时
  与需求方确认——默认在父任务收口执行）。

## Acceptance Criteria

- [ ] AC1：compose 环境下经 nginx 请求，后端日志/限流 key 中出现真实客户端 IP
      而非 nginx 容器 IP。
- [ ] AC2：直连 backend:8000 并携带伪造 `X-Forwarded-For: 1.2.3.4`，限流身份
      不受该头影响（仍按直连来源 IP 计数）。
- [ ] AC3：同一真实 IP 压测 model 桶路径触发 429；不同真实 IP 各自独立计数
      （覆盖 `08-11` AC5 原有语义）。
- [ ] AC4：`FORWARDED_ALLOW_IPS` 未配置时（本地 dev），行为与现状完全一致，
      既有测试全绿。

## Constraints

- 不改 `RateLimitMiddleware` 的桶分类与响应形状（桶语义由
  `08-15-model-gateway-budget-circuit` 与 08-11 R4 另行处理）。
- nginx 配置改动与 `08-15-public-exposure-surface` 的 nginx 改动
  （client_max_body_size）可能同文件冲突：本任务先合入（见父任务衔接声明）。
