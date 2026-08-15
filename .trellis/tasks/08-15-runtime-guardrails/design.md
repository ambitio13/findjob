# Design：运行时护栏（熔断可见性 / 账号锁定 / 容器资源）

## 0. 边界

- **改动面**：`app/api/v1/health.py`（新增字段）、`app/services/auth_service.py`
  （锁定逻辑）、`app/schemas/api.py`（HealthResponse）、
  `frontend/src/types`（HealthResponse 可选字段）、`docker-compose.yml`
  （资源限制 + healthcheck）。
- **依赖产出**（已核实存在性）：#1 的 `budget.is_tripped_today()` 只读
  函数与 tripped Redis key；#2 的真实 IP 链路（仅用于验收，不在代码内）。
- **不引入**：Prometheus / Sentry / alertmanager 等外部依赖。

## 1. 熔断可见性（D4）

### 1.1 数据源

复用 #1 的 `mb:{ns}:tripped:{UTCyyyymmdd}` key（`SET EX 172800`），
本任务**只读**，不新增写入方。单真相源，随 UTC 日滚动自动复位。

### 1.2 health 暴露

`HealthResponse` 增加字段：

```python
model_budget_tripped: bool = False
```

`health()` 调用 `budget.is_tripped_today()`；Redis 故障时返回 `False`
（fail-open，health 不因观测功能降级——与全站 fail-open 策略一致）。
注意：熔断触发后 `status` 字段仍为 `"ok"`（health 端点不映射熔断态），
**监控脚本必须检查 `model_budget_tripped` 字段而非 status**，写进 runbook。

- 日志：超限瞬间 ERROR（#1 已在触发点打 WARN）→ 本任务把 #1 的
  `model_budget.tripped` 事件**升为 ERROR** 并补当日 count/limit 字段
  （一行改动，归属本任务避免 #1 范围膨胀）。
- 选 health 字段而非独立端点：health 已被 compose healthcheck、dev
  脚本、前端（`client.ts:69`）消费，是现成的观测面；新增端点反而扩大
  暴露面。字段向后兼容（新增 key），前端 TS 类型加 `model_budget_tripped?:`
  可选字段即可，不改 UI（或运维断言用）。

### 1.3 已核实

前端确实消费 `GET /health`（`client.ts:69`）——加字段向后兼容，无破坏。

## 2. 账号级登录锁定（E2）

### 2.1 位置：`auth_service.login` 内部

```python
def login(db, *, username, password):
    if _is_locked(username):            # Redis GET lk:{ns}:lock:{username}
        raise AuthError("invalid_credentials")   # 与凭证错误完全同形
    try:
        ... 现有验证逻辑（含 unknown-user 的 dummy verify）
    except 凭证失败:
        _record_failure(username)       # INCR + 首次 EXPIRE 900
        if failures >= 5: SET lk:{ns}:lock:{username} EX 900
        raise
    _clear_failures(username)           # DEL 计数与锁
    return user, token
```

- **锁定的语义**：锁定期间**正确密码也 401**（AC2）。防爆破优先于可用性；
  15 分钟窗口 + 5 次阈值下正常用户几乎不可能触发。
- **错误同形**：锁定、未知用户、错误密码全部
  `AuthError("invalid_credentials")`——不给爆破者"该账号存在且已被锁"
  的信号（AC3）。
- **键名**：`lk:{queue_namespace}:fail:{username}` 与
  `lk:{queue_namespace}:lock:{username}`，TTL 900s（= 锁定窗口，计数
  窗口与锁定窗口对齐，无永久累积）。
- **键安全（已修正）**：`USERNAME_PATTERN` 仅在**注册** schema 生效；
  登录的 `AuthLoginRequest.username` 只有 min/max_length，可传任意字节
  （含 `:`、超长串）。因此锁定逻辑入口必须先校验：username 不匹配
  `USERNAME_PATTERN` 则**跳过计数/查锁**（直接走正常验证流程，未知用户
  仍得 invalid_credentials）。合法用户名必过 pattern（注册已验证），
  锁定保护不丢失；同时堵住任意字节进 Redis key 与无限新 key 两个面。
- **Redis 故障 fail-open**：`_is_locked/_record_failure` 整体 try/except
  吞掉并 WARN——登录可用性优先，与限流/熔断策略一致（AC5）。
- dummy-verify 时序对齐：locked 分支直接 raise，不做 PBKDF2——锁定态的
  响应时差理论上可探测（快于真实验证），但锁定态本身就证明爆破在进行，
  无新增信息泄露，可接受（写进代码注释）。

### 2.2 否决的替代方案

| 方案 | 否决理由 |
|---|---|
| FastAPI middleware 层拦 `/auth/login` | 拿不到"用户名验证失败"这一业务结果，只能按 IP 计数（已有 auth 桶） |
| DB 字段记录锁定 | 引入迁移与写放大；锁定是易失的短窗状态，Redis 语义刚好 |
| 按 IP+用户名双维度 | IP 可变（正是 #2 要防的伪造面），用户名维度才是不可绕过的锚点 |

## 3. 容器资源与健康检查（D2）

### 3.1 compose（dev 与 08-11 的 prod 变体同构）

```yaml
backend:
  deploy:
    resources:
      limits: { cpus: "1.0", memory: 1g }
  healthcheck:
    test: ["CMD", "python", "-c",
           "import urllib.request as u; u.urlopen('http://localhost:8000/api/v1/health', timeout=3)"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 30s

worker:
  deploy:
    resources:
      limits: { cpus: "1.0", memory: 1536m }   # worker 承载解析+LLM，给更高
```

- 选 `deploy.resources.limits`：docker compose v2 非 swarm 也生效，
  且未来迁 swarm 不改写。
- healthcheck 用 python 而非 curl：python:3.11-slim 无 curl/wget，
  python 必在——避免改 Dockerfile 装包（D1 镜像瘦身属已知取舍，不在此做）。
- worker 不加 healthcheck：arq 无 HTTP 面，slim 镜像无 pgrep（procps
  未装）。退化为 `restart: unless-stopped`（已有）+ runbook 说明
  `docker compose ps` 人工确认。加进程检查需装 procps，收益不抵镜像
  变更——记录为取舍。
- 数值依据：2C4G VPS，postgres+redis+system 预留 ~1g，backend 1g、
  worker 1.5g 封顶后互不挤兑；超限被 OOM-kill 后 restart 拉起，
  比 OOM 整机好。

### 3.2 与其他任务的 compose 冲突

#2（command/网络）、#3（开关默认值）都改 compose；本任务最后合入，
冲突面最小（resources/healthcheck 是新增块）。

## 4. 测试设计

- 熔断可见性：fake Redis 注入 tripped key → health 返回 true；无 key →
  false；Redis 异常 → false 不抛（AC1 前半 + AC5 风格）。日志 ERROR
  断言进 #1 的触发点测试（本任务改级别后同步改断言）。
- 锁定：FakeRedis 计数——5 次失败后第 6 次正确密码 401（AC2）；TTL
  过期后可登录（fake 支持时间前进或直接 DEL 模拟）；成功登录清零；
  锁定响应与错误密码响应逐字节一致（AC3）；Redis 异常路径登录正常（AC5）。
- compose：`docker compose config` 校验通过进 CI（与 #2/#3 的配置
  断言脚本合并）；AC4 的 kill worker 演练属 runbook 手工项。

## 5. 兼容性与回滚

- HealthResponse 加字段向后兼容；旧前端忽略新 key。
- 锁定为纯登录路径内新增，注册/鉴权依赖（`deps.py`）不动。
- 资源限制若过紧（OOM 频繁）调 compose 数值即可，无需回滚代码。
- 三块改动相互独立，可分别 revert。
