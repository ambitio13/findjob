# 执行记录：模型日预算熔断下沉到 ModelGateway 调用层

## 改动清单

### 1. 新增 `app/models_gateway/budget.py`
- `ModelBudgetExceeded` 异常类（携带 count/limit/date）。
- `ensure_budget()`: Redis INCR 日计数 key `mb:{ns}:daily:{UTCyyyymmdd}`，超限抛异常并写 tripped marker key。
- `is_tripped_today()`: 只读探测，供 #5 health 端点消费。
- `BudgetedModelGateway(inner)`: 装饰器，在 `chat()` / `structured()` 前置 `ensure_budget()`。
- `BUDGET_EXCEEDED_ERROR = "model budget exceeded"` 常量，供 worker 落库使用。
- INCR-then-check 语义（被拒尝试也消耗计数）；48h TTL 跨时区兜底。
- fail-open 默认（Redis 故障时放行），可配置 fail-closed。

### 2. `app/core/config.py` 新增配置项
- `model_daily_call_limit: int = 500`（与 08-11 决策对齐）
- `model_daily_budget_enabled: bool = True`
- `model_budget_fail_open: bool = True`

### 3. `app/models_gateway/factory.py` 包装
- `get_model_gateway()` 返回 `BudgetedModelGateway(inner)`，覆盖 HTTP + worker 两条路径。

### 4. `app/main.py` 全局异常映射
- `ModelBudgetExceeded` → 429 + `Retry-After`（到 UTC 午夜秒数）+ `{"detail": {"reason": "model_budget_exceeded", "retry_after_seconds": N}}`。

### 5. `app/queue/handlers.py` 4 处 LLM handler
- 在 `except Exception` 之前新增 `except ModelBudgetExceeded` 分支，调用 `fail_run(error=BUDGET_EXCEEDED_ERROR)` + `return "failed"`。
- 涉及: `jd_paste_parsing`, `resume_fact_extraction`, `resume_aware_jd_analysis`, `readiness_generation`。

### 6. `app/api/rate_limit.py` 注释更新
- 模块 docstring 和 `_MODEL_PATH_FRAGMENTS` 注释标注：分钟级桶只是请求体验保护，费用防线在 `budget.py`。

### 7. 测试 `app/tests/test_model_budget.py` (15 tests)
- ensure_budget: 未超限放行 / 超限抛异常 / tripped marker 写入 / TTL 设置 / disabled no-op / fail-open / fail-closed / is_tripped fail-open。
- BudgetedModelGateway: chat 放行 / chat 超限抛异常 / structured 超限抛异常 / provider_name 保留。
- HTTP handler: 429 + Retry-After + reason 字段。
- Factory: 返回 BudgetedModelGateway。
- 常量: BUDGET_EXCEEDED_ERROR 值断言。

## 测试结果
```
15 passed (test_model_budget.py)
1045 passed (full suite, +15 vs baseline 1030, no regressions)
```
