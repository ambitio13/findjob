# 父任务收口：08-15-adversarial-security-remediation

## 子任务完成状态

| # | 子任务 | 状态 | implement.md | 测试 |
|---|--------|------|-------------|------|
| 1 | 08-15-model-gateway-budget-circuit | done | ✅ | 13 tests |
| 2 | 08-15-proxy-real-ip-trust-chain | done | ✅ | 8 tests |
| 3 | 08-15-public-exposure-surface | done | ✅ | 9 tests |
| 4 | 08-15-content-key-decouple | done | ✅ | 16 tests |
| 5 | 08-15-runtime-guardrails | done | ✅ | 20 tests (health 4 + lockout 7 + container 6 + budget log 1 + existing 2) |

## 跨子任务集成验收

### 1. 全量回归测试
- **1070 passed, 0 failed**（含全部 08-15 子任务新增测试 + 既有 08-11 测试套件）

### 2. docker compose config 校验
- `docker compose config -q` 通过（零输出），compose 编排无语法错误

### 3. 子任务联合测试
- 66 tests passed（7 个 08-15 测试文件联合运行，无跨测试污染）

### 4. 衔接声明落实

#### 对 08-11 R4 的影响（已回写 08-11 prd）
- R4 "每日模型预算熔断"实现口径已更新：由 `08-15-model-gateway-budget-circuit`
  取代原"model 桶命中时 Redis 按日计数"，改为以 `ModelGateway` 真实 LLM 调用
  计数（`chat` + `structured`）
- AC11 验收方式相应调整：用任意触发 `ModelGateway.chat`/`structured` 的端点
  验证，不再限定 model 桶 HTTP 路径；增加 `model_budget_tripped` 字段检查

#### 对 08-11 R3 的影响（.env.example 落地）
- `.env.example` 已新增全部 08-15 配置项：
  - `CONTENT_ENCRYPTION_KEY=`（子任务 #4）
  - `FORWARDED_ALLOW_IPS=127.0.0.1`（子任务 #2）
  - `MODEL_DAILY_CALL_LIMIT=500` / `MODEL_DAILY_BUDGET_ENABLED=true` / `MODEL_BUDGET_FAIL_OPEN=true`（子任务 #1）
  - `AUTH_LOGIN_MAX_FAILURES=5` / `AUTH_LOCKOUT_MINUTES=15`（子任务 #5）

#### 对 docker-compose 的影响（合入顺序 #2 → #3 → #5）
- #2：appnet 子网 + frontend 固定 IP + backend `--proxy-headers`
- #3：nginx body limit + prod docs/bridge 关闭
- #5：resource limits + healthcheck + depends_on condition
- 三者合入无冲突，compose config 校验通过

### 5. Phase 3 集成验收清单

- [x] 全部子任务归档（status=done, completedAt=2026-08-15）
- [x] 全量回归 1070 passed
- [x] compose config 校验通过
- [x] 08-11 prd 已按衔接声明更新（R4 + AC11）
- [x] .env.example 含全部新增配置项
- [ ] **生产环境拉起验证**（需 VPS + 域名，属用户侧操作，不在本任务自动化范围）：
  - 伪造 `X-Forwarded-For` 不能绕过限流
  - 上传简历触发的 LLM 调用计入日预算
  - `/docs`、`/openapi.json`、`/userscript-bridge/*` 在 prod 不可达
  - 12MB 文件上传收到应用层 413

## Explicitly Out of Scope（与父任务 PRD 一致，记录为已知取舍）

- C2 简历原件明文落盘
- D1 镜像 root + dev 依赖
- E1 注册接口用户名枚举
- 数据备份、token 吊销/refresh、密钥轮换自动化

## 父任务状态

全部 5 个子任务已完成并通过测试，跨任务衔接声明已落实，08-11 prd 已回写。
生产环境拉起验证属用户侧操作（需 VPS + 域名），不在本自动化任务范围内。
父任务标记为 done。
