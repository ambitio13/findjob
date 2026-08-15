# Implement：运行时护栏 — 熔断告警、账号锁定、容器资源限制

## 改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/models_gateway/budget.py` | 熔断触发时日志级别 `warning`→`error`（D4 可观测）；`is_tripped_today()` 只读探针供 health 端点调用 |
| `backend/app/schemas/api.py` | `HealthResponse` 新增 `model_budget_tripped: bool = False` |
| `backend/app/api/v1/health.py` | 调用 `budget.is_tripped_today()` 返回熔断状态；import 改为模块级以支持 monkeypatch |
| `backend/app/services/auth_service.py` | 账号级登录锁定：`_is_locked` / `_record_failure` / `_clear_failures`，用户名维度 Redis 计数，5 次失败锁 15 分钟，fail-open on Redis error；非模式用户名跳过锁定路径 |
| `backend/app/core/config.py` | 新增 `auth_login_max_failures: int = 5`、`auth_lockout_minutes: int = 15` |
| `docker-compose.yml` | backend/worker 加 `deploy.resources.limits`（1g/1536m）；backend 加 healthcheck（python urllib）；frontend `depends_on` 改为 `condition: service_healthy` |
| `.env.example` | 新增 `AUTH_LOGIN_MAX_FAILURES=5`、`AUTH_LOCKOUT_MINUTES=15` 文档 |
| `frontend/src/types/index.ts` | `HealthResponse` 接口新增 `model_budget_tripped?: boolean` |

## 新增测试

| 文件 | 测试 |
|---|---|
| `test_health.py`（重写） | 4 个：基础健康、含 `model_budget_tripped` 字段、熔断时返回 true、默认 false |
| `test_auth_lockout.py`（新建） | 7 个：5 次锁定(AC2)、阈值下不锁(AC2)、成功清零(AC2)、锁定响应与错误密码一致(AC3)、Redis 故障 fail-open(AC5)、非模式用户名跳过、配置默认值 |
| `test_container_resources.py`（新建） | 6 个：backend/worker 资源限制、worker 内存 > backend、backend healthcheck、frontend depends_on healthy、compose config 校验(AC4) |
| `test_model_budget.py`（+1） | `test_ensure_budget_tripped_logs_error`：stdout 捕获验证 ERROR 级别日志(D4) |

## AC 对照

- **AC1（熔断可见性）**：`budget.py` 熔断时 `_log.error("model_budget.tripped", ...)` + Redis tripped key；`health.py` 调用 `is_tripped_today()` 返回 `model_budget_tripped`；`test_health_reports_tripped_when_budget_exceeded` + `test_ensure_budget_tripped_logs_error` 验证。
- **AC2（账号锁定）**：5 次错误密码后正确密码也 401，成功登录清零——`test_account_locked_after_max_failures`、`test_successful_login_clears_failure_counter`。
- **AC3（无枚举面）**：`test_locked_response_identical_to_wrong_password` — 锁定与错误密码响应逐字节一致。
- **AC4（compose 校验）**：`test_compose_config_valid` + 资源限制/healthcheck/depends_on 各有专测。
- **AC5（fail-open + 全绿）**：`test_lockout_fail_open_on_redis_error` 注入 broken Redis 验证登录不受影响；全量 1070 passed。

## 关键设计决策落实

1. **锁定 key 格式**：`lk:{queue_namespace}:fail:{username}` / `lk:{queue_namespace}:lock:{username}`，TTL 900s。
2. **USERNAME_PATTERN 守卫**：只有模式合法的用户名参与锁定，阻止任意字节进入 Redis key。
3. **health.py import 改模块级**：`from app.models_gateway import budget`（非函数级 import），使 monkeypatch 能生效。
4. **fail-open 策略**：Redis 故障时锁定功能降级（`_is_locked` 返回 False，`_record_failure`/`_clear_failures` 静默），与限流策略一致。
5. **structlog 日志捕获**：structlog 配置 `PrintLoggerFactory` 直接写 stdout，绕过 stdlib logging 树，测试改用 stdout 捕获验证 ERROR 级别。

## 测试结果

- Task 5 专测：33 passed
- 全量回归：1070 passed，0 failed
