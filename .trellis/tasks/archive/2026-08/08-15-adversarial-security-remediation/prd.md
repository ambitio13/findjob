# 对抗性审查整改：上线阻断级安全修复（父任务）

## Background

2026-08-15 对抗性审查（面向展示型公网部署）发现了一批**在 `08-11-showcase-demo-deployment`
R1–R8 规划之外**的漏洞。本父任务总控这些整改；`08-11` 的既有规划继续由该任务自管，
本父任务不重复其范围（前端鉴权、种子数据、TLS、CI 等）。

## 子任务与依赖顺序（显式声明，不靠树形位置暗示）

```
Phase 1（可并行，无相互依赖）:
  1. 08-15-model-gateway-budget-circuit   (P0, A1) —— 模型日预算熔断下沉到 gateway 调用层
  2. 08-15-proxy-real-ip-trust-chain      (P0, A2) —— 反代真实 IP 信任链 + XFF 伪造防护
  3. 08-15-public-exposure-surface        (P0, B1+B2+B3) —— docs/bridge/nginx 收敛
  4. 08-15-content-key-decouple           (P1, C1) —— 加密密钥与签名密钥解耦

Phase 2（依赖 Phase 1 的产出）:
  5. 08-15-runtime-guardrails             (P1, D2+D4+E2)
     - 熔断告警依赖 #1 的熔断器存在；
     - 账号锁定的"伪造 IP 绕过"验收依赖 #2 的真实 IP 链路；
     - 资源限制独立，但统一在此任务落地。

Phase 3（父任务收口，依赖全部子任务完成）:
  - 跨子任务集成验收 + 与 08-11 任务的衔接声明。
```

## 跨任务衔接（Interface Contracts）

- **对 08-11 R4 的影响**：R4 的"每日模型预算熔断（model 桶按日计数）"实现口径
  由子任务 #1 **取代**——熔断以 `ModelGateway` 真实调用计数，不再以 HTTP 路径分类
  为准。`08-11` 的 AC11 验收方式相应调整（用任意触发 LLM 的端点验证，而非仅
  model 桶路径）。需回写 `08-11` prd（在本父任务收口时执行）。
- **对 08-11 R3 的影响**：`.env.prod.example` 必须新增子任务 #4 的
  `CONTENT_ENCRYPTION_KEY` 与 #1 的日预算配置项；该文件定稿前 #1/#4 需合入。
- **对 docker-compose 的影响**：#2/#3/#5 均改 compose / nginx / uvicorn 启动参数，
  合入时按 #2 → #3 → #5 顺序解冲突。

## 父任务自身验收（Phase 3）

- [ ] 全部子任务归档，各自的 AC 通过。
- [ ] 在一个干净环境按 prod 编排拉起，验证：
      - 伪造 `X-Forwarded-For` 不能绕过限流（#2 AC）；
      - 上传简历触发的 LLM 调用计入日预算（#1 AC，即 08-11 AC11 的强化版）；
      - `/docs`、`/openapi.json`、`/userscript-bridge/*` 在 prod 不可达（#3 AC）；
      - 12MB 文件上传收到应用层 413 而非 nginx 413（#3 AC）。
- [ ] `08-11` prd 已按上述衔接声明更新。

## Explicitly Out of Scope（记录为已知取舍，不建任务）

- C2 简历原件明文落盘（README/关于页需如实说明隐私加密仅覆盖 DB 长文本）；
- D1 镜像 root + dev 依赖（展示场景接受，升级为生产多租户前必须改）；
- E1 注册接口用户名枚举（邀请码关闭注册时影响可控）；
- 数据备份、token 吊销/refresh、密钥轮换自动化（08-11 已明确 out of scope）。
