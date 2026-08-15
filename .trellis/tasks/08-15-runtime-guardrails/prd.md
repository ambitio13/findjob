# 运行时护栏：熔断告警、账号锁定、容器资源限制

## 依赖声明

- **Depends on**:
  - `08-15-model-gateway-budget-circuit`（D4 告警挂在其熔断事件之上）；
  - `08-15-proxy-real-ip-trust-chain`（E2 锁定的"伪造 IP 绕过"验收需要真实
    IP 链路先成立）。
  - 资源限制部分（D2）无依赖，可先行。
- **Blocks**: 父任务 `08-15-adversarial-security-remediation` 收口验收。

## Background（漏洞 D2 + D4 + E2）

1. **D4 — 零运行时可观测**：`08-11` R7 只有 CI。日预算熔断（整改后存在于
   gateway 层）触发后没有任何人知道；429 激增、worker 停摆、resume 卷磁盘满
   全部静默。熔断是展示部署的核心安全机制，触发却不可见。
2. **E2 — 无账号级登录锁定**：auth 桶 10/min/IP 是爆破唯一防线，配合 XFF
   伪造（由 proxy-real-ip-trust-chain 封堵）风险降低，但仍无账号级兜底；
   PBKDF2 39 万次迭代只是减速。
3. **D2 — 容器无资源限制**：compose 对 backend/worker 无 mem/cpu 限制，
   一个失控的解析任务或并发 LLM 调用可 OOM 整台 2C4G VPS；backend/worker
   也无 healthcheck。

## Goal

熔断可见、账号可锁、容器资源有界。最小实现，不引入重型监控栈。

## Requirements

- R1 **熔断可见性**：预算熔断触发时（依赖 gateway 熔断器的事件点）：
  - 结构化 ERROR 日志（含当日计数、上限）；
  - `/api/v1/health` 响应增加 `model_budget_tripped: bool`（当日是否触发过）；
  - 熔断状态写入 Redis（当日 key），随日滚动自动复位。
- R2 **账号级登录锁定**：用户名维度 Redis 计数，连续失败 N 次（默认 5）锁定
  M 分钟（默认 15），锁定期间登录直接 401（reason 复用 `invalid_credentials`，
  不新增可探测的错误差异）；成功登录清零计数；key 带 TTL 防泄漏累积。
- R3 **容器资源限制与健康检查**：
  - compose（及 prod 变体）为 backend/worker 设 `deploy.resources.limits`
    或 `mem_limit`（按 2C4G VPS 预算给出保守值，如 backend 1g、worker 1.5g）；
  - backend 增加 compose healthcheck（curl `/api/v1/health`）；worker 用
    arq 进程存活检查或退化为 restart 策略说明。
- R4 上述行为各有测试（熔断状态暴露、锁定/解锁/复位、配置默认值）。

## Acceptance Criteria

- [ ] AC1：触发日预算熔断后，`/api/v1/health` 返回 `model_budget_tripped=true`，
      且日志出现对应 ERROR 事件；次日自动复位为 false。
- [ ] AC2：同一用户名连续 5 次错误密码后，第 6 次**正确密码**也返回 401，
      15 分钟后（或 TTL 到期）正确密码可登录；成功登录清零失败计数。
- [ ] AC3：锁定错误与普通凭证错误响应完全一致（无枚举面）。
- [ ] AC4：compose config 校验通过，资源限制与健康检查就位；
      手动 kill worker 后按策略重启。
- [ ] AC5：既有测试全绿；锁定功能对 Redis 故障 fail-open（登录不受影响），
      与限流策略一致并在注释说明。

## Constraints

- 不引入 Prometheus/Sentry 等外部依赖（展示部署最小化；升级路径在注释中说明）。
- health 响应形状变更需同步前端健康指示（若有）——实现前检查 frontend 是否
  消费该端点。
- 本任务合入顺序在 `08-15-proxy-real-ip-trust-chain` 之后（compose 冲突）。
