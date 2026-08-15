# Design：模型日预算熔断下沉到 ModelGateway 调用层

## 0. 边界

- **改动面**：`app/models_gateway/`（新增 budget 模块 + factory 包装）、
  `app/core/config.py`（新配置项）、`create_app`（注册全局异常映射）、
  `app/queue/handlers.py` 的 run 失败落库路径（识别新异常 reason）。
- **不改动**：`deepseek.py` / `fake.py` / `base.py` 的接口与实现；
  `RateLimitMiddleware` 的桶分类与 429 响应形状；所有业务调用方
  （executor / service 层零改动）。

## 1. 核心决策：factory 层装饰器收口

### 选型

新增 `BudgetedModelGateway(ModelGateway)` 装饰器，包住具体 provider，
由 `get_model_gateway()` 统一返回：

```python
# factory.py（改后示意）
inner = DeepSeekModelGateway(...) or FakeModelGateway()
return BudgetedModelGateway(inner)          # 预算检查在 chat/structured 前置
```

### 为什么可行（已核实）

- API 同步路径：`get_model_gateway_dep` → `get_model_gateway()`；
- worker 异步路径：`app/queue/handlers.py` 4 处全部调用
  `from app.models_gateway.factory import get_model_gateway`；
- **factory 是两条路径的唯一解析点**，包一层即全量覆盖，业务代码零感知。

### 否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 在 `base.ModelGateway.chat/structured` 里插检查 | 污染 provider 中立接口；fake/deepseek 都要感知预算 |
| 在每个 executor/service 调用点包 try | 调用点分散，新增调用点必然漏（与 HTTP 路径白名单同病） |
| 只在 worker 层拦 | 覆盖不了 discovery 等同步 LLM 端点 |

## 2. 熔断逻辑（`app/models_gateway/budget.py`）

```
chat(request) / structured(request):
  1. ensure_budget()                     # Redis INCR 日计数 key
  2. 若 count > limit:
       写 tripped 标记 key（供 #5 的 health 暴露，本任务只留钩子）
       log WARN("model_budget.tripped", count, limit, date)
       raise ModelBudgetExceeded(count=count, limit=limit)
  3. return await self._inner.chat(...)  # 放行到真实 provider
```

- **INCR-then-check**（先计数再判断）：被拒绝的尝试同样消耗计数。理由：
  防止超限后高频重试刷 key；实现最简；"预算按调用尝试计"语义清晰。
- **日 key**：`mb:{queue_namespace}:daily:{UTCyyyymmdd}`，首次 INCR 时
  `EXPIRE 172800`（48h，跨时区窗口兜底）。
- **tripped key**：`mb:{queue_namespace}:tripped:{UTCyyyymmdd}`，`SET EX 172800`；
  暴露 `is_tripped_today() -> bool` 只读函数（#5 消费，本任务仅实现+单测）。

## 3. 异常与错误映射

- `ModelBudgetExceeded` 定义在 `app/models_gateway/budget.py`，携带
  `count` / `limit` / `date`。
- **HTTP 路径**：`create_app()` 注册全局 exception handler，捕获
  `ModelBudgetExceeded` → `429`，body `{"detail": {"reason":
  "model_budget_exceeded", "retry_after_seconds": <到 UTC 午夜的秒数>}}`，
  带 `Retry-After` 头。与现有限流 429 的 detail 形状约定一致
  （`reason` 字段区分两者）。选择全局 handler 而非逐端点 try：调用方
  分散且会继续增加，逐点映射必漏。
- **worker 路径**：现有 handler 的落库契约是** sanitized 常量**（如
  `"resume fact extraction failed"`），handlers.py 明确禁止原始异常文本
  入库——因此**不能**靠"保留异常类名"实现 AC1 可识别。正确做法：在 4 处
  LLM handler 的通用 `except Exception` **之前**增加独立分支：

  ```python
  except ModelBudgetExceeded:
      fail_run(payload.agent_run_id, error="model budget exceeded")
      return "failed"
  ```

  AC1 断言 run error == 该常量。改动点 = handlers.py 4 处，属本任务边界内
  （与第 0 节声明一致）。

## 4. 配置项（`app/core/config.py`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `model_daily_call_limit: int` | `500` | 与 08-11 既有决策对齐（500/日） |
| `model_daily_budget_enabled: bool` | `True` | 测试/本地可关 |
| `model_budget_fail_open: bool` | `True` | Redis 故障时策略 |

**fail-open vs fail-closed**：默认 fail-open，与 `RateLimitMiddleware`
策略一致（限流/熔断是费用护栏不是授权边界；Redis 挂了不该打断正常演示）。
runbook 将服务商侧 Key 上限标注为**强制**二道防线。`model_budget_fail_open=false`
留给未来想要 fail-closed 的部署，本次不额外测试矩阵（仅单测覆盖默认分支）。

## 5. fake provider 行为与测试注入点

`FakeModelGateway` 同样被包装（factory 决定，不区分 provider）。测试通过
`model_daily_call_limit=N` 小值 + fake provider 确定性验证 AC1–AC4，
不经网络。本地开发默认 limit=500 基本无感。

**Redis 注入点（测试必需）**：`ensure_budget()` 内部解析 Redis 时沿用
`RateLimitMiddleware` 的模式——budget 模块暴露可注入参数
（如 `check_budget(redis_client=None)`，`None` 时惰性解析
`get_redis_client()`），集成测与单测注入 FakeRedis，import 不触网。

## 6. 与 `_MODEL_PATH_FRAGMENTS` 的关系（R4）

`rate_limit.py` 模块 docstring 与 `_MODEL_PATH_FRAGMENTS` 注释更新为：
"分钟级桶分类仅是请求体验保护；费用防线在
`app/models_gateway/budget.py`（每次真实调用计数）"。代码不动。

## 7. 兼容性与回滚

- 纯新增模块 + factory 一行包装，业务调用方零改动；关掉
  `model_daily_budget_enabled` 即回到现状，回滚 = revert 单个 commit。
- Redis key 新增两个前缀（`mb:`），与 `rl:`/arq key 无冲突。
- 不改 DB schema，无迁移。

## 8. 测试设计

- 单测：fake Redis 注入（沿用 `test_rate_limit.py` 的 FakeRedis 模式），
  覆盖未超限放行 / 超限抛异常 / Redis 故障 fail-open / tripped key 写入 /
  `is_tripped_today` 读取 / TTL 设置调用。
- 集成测（TestClient + fake provider + 小 limit）：
  - AC1：`POST /resumes`（global 桶）N+1 次后 run failed 且 error 含
    `model_budget_exceeded`；
  - AC2：discovery 与 `/jobs/{id}/analysis` 计入同一计数器（断言 Redis
    计数在混合调用后等于总调用数）；
  - HTTP 同步端点直接触发时返回 429 + `Retry-After`。
- AC3（次日恢复）：单测用可注入的 `now()`/date 函数或直接构造昨日 key
  断言新日期 key 独立。
