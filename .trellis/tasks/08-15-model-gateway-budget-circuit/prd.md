# 模型日预算熔断下沉到 ModelGateway 调用层

## 依赖声明

- **Depends on**: 无（Phase 1，可与其他 Phase 1 子任务并行）。
- **Blocks**: `08-15-runtime-guardrails`（熔断告警需要熔断器先存在）；
  `08-11-showcase-demo-deployment` R4 的实现口径（由本任务取代）。

## Background（漏洞 A1）

`RateLimitMiddleware` 按 HTTP 路径片段把请求分入 model/global 桶
（`app/api/rate_limit.py` `_MODEL_PATH_FRAGMENTS`），`08-11` R4 规划的日预算
也基于"model 桶命中"计数。但以下**真实触发 LLM 调用**的端点全部落进 global 桶
（300/min），分钟限流与日熔断双双不计数：

| 端点 | 触发的模型调用 |
|---|---|
| `POST /resumes` | worker 队列的简历事实抽取 LLM |
| `POST /resumes/{id}/versions/{vid}/extract` | 同上（重抽取） |
| `/boss/recommended-jobs/discovery` 系列 | 同步 match 决策 LLM |

认证用户可循环上传最小合法 PDF 接近 300 次/分钟，绕过全部费用防线。

**根因**：费用控制在 HTTP 层做，消耗发生在 models_gateway。路径白名单永远追不上
新端点（`boss_recommended_jobs.py` 的 router prefix 已统一为
`/boss/recommended-jobs`，路径分类天然脆弱）。

## Goal

日预算熔断下沉到 `ModelGateway` 调用点：每一次真实的模型调用（HTTP 同步调用 +
worker 队列异步调用）统一计数与熔断，HTTP 路径分类降级为纯粹的体验保护。

## Requirements

- R1 在 `app/models_gateway` 内实现日级预算熔断：每次 `complete`（或等价调用）
  前检查 Redis 日计数，超过可配置上限（如 `MODEL_DAILY_CALL_LIMIT`，默认给一个
  保守值）则抛出稳定的业务错误（如 `ModelBudgetExceeded`），端点/worker 统一
  映射为 429 / run failed（reason=model_budget_exceeded）。
- R2 计数与判断在 gateway 内完成，HTTP 层（同步端点）与 arq worker（异步任务）
  两条路径都必须经过同一实现；不允许各自为政。
- R3 Redis 故障时的策略必须显式选择并在代码注释 + 配置说明中记录（建议：熔断
  依赖 fail-closed 可选、默认与现有限流一致的 fail-open，但 runbook 中服务商侧
  Key 上限为强制二道防线）。
- R4 现有 `_MODEL_PATH_FRAGMENTS` 分钟级限流保留不动（体验保护），但其注释需
  更新，声明它不再是费用防线。
- R5 超限事件写结构化 WARN 日志（后续 `08-15-runtime-guardrails` 在此之上加告警）。
- R6 日 key 按 UTC 日滚动，带 TTL（如 48h），命名纳入 `queue_namespace` 隔离。

## Acceptance Criteria

- [ ] AC1：将日上限设为 N，通过 `POST /resumes`（global 桶路径）触发 N+1 次
      真实 LLM 调用，第 N+1 次调用被拦截（worker run 状态 failed、error 为
      可断言的 sanitized 常量，具体值见 design 第 3 节），证明计费盲区已封堵。
- [ ] AC2：同样方式验证 `/boss/recommended-jobs/discovery` 与 model 桶路径
      （如 `/jobs/{id}/analysis`）计入同一日计数器。
- [ ] AC3：次日（或 Redis key 过期后）自动恢复。
- [ ] AC4：fake provider（本地/测试）路径有确定性测试覆盖（不经网络）。
- [ ] AC5：单元测试覆盖 Redis 故障分支，行为与声明的 fail 策略一致。

## Constraints

- 不削弱既有安全不变量；不改变现有分钟级限流对外行为（429 响应形状保持）。
- 与 `08-11` AC11 衔接：AC11 的验收改用本任务 AC1 的方法。
