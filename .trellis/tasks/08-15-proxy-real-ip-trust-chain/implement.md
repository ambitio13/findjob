# Implement：反代真实 IP 信任链与限流防伪造

## 执行顺序

### Step 1 — compose 专属网络 + frontend 固定 IP
- [x] `docker-compose.yml`：新增 `appnet` 子网 `172.28.0.0/24`，所有服务
  (postgres/redis/backend/worker/frontend) 显式接入；frontend 固定 IP
  `172.28.0.10`。
- [x] 关键坑已处理：声明 `networks` 后默认网络断开，所有服务都接入 appnet。

### Step 2 — backend 启动参数
- [x] compose backend `command` 覆盖 Dockerfile CMD：
  `uvicorn ... --proxy-headers --forwarded-allow-ips=$${FORWARDED_ALLOW_IPS:-127.0.0.1}`
- [x] backend `environment` 注入 `FORWARDED_ALLOW_IPS`。
- [x] 默认 `127.0.0.1` 保持本地直连行为不变。

### Step 3 — nginx 核对（零逻辑改动）
- [x] `frontend/nginx.conf` 已是
  `X-Forwarded-For $proxy_add_x_forwarded_for` + `X-Real-IP $remote_addr`，
  仅补注释说明"勿改为直接透传客户端头"。

### Step 4 — env 文档
- [x] `.env.example` 新增 `FORWARDED_ALLOW_IPS` 与"禁止 *"注释。

### Step 5 — 配置断言测试（CI 可自动化）
- [x] `app/tests/test_proxy_real_ip_config.py`：8 项断言
  (backend command 含 --proxy-headers、allow-ips 非 *、frontend 固定 IP、
  appnet 子网、全服务接入、nginx 用 $proxy_add_x_forwarded_for、
  env 文档含 FORWARDED_ALLOW_IPS)。

### Step 6 — 验证命令

```bash
# 配置断言测试（CI 自动化层）
QUEUE_NAMESPACE=job-search-agent-test \
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/job_search_agent_test \
.venv/bin/python -m pytest app/tests/test_proxy_real_ip_config.py -v

# compose 语法校验
docker compose config -q

# e2e 手工层（runbook，AC1/AC2/AC3）
# 1) compose 拉起后，经 nginx 请求，后端 rate_limit.* 日志的 key 应为真实 IP
# 2) 直连 backend:8000 携带伪造 X-Forwarded-For: 1.2.3.4，限流身份不变
# 3) 同一真实 IP 压测 model 桶路径触发 429
```

## 验收映射

| AC | 验证方式 |
|----|---------|
| AC1 (compose 经 nginx 真实 IP) | runbook 手工：看后端限流 key 日志 |
| AC2 (直连伪造 XFF 不影响身份) | runbook 手工：curl 直连 backend:8000 |
| AC3 (同一 IP 压测 429) | runbook 手工 |
| AC4 (本地 dev 行为不变) | 配置断言：默认 127.0.0.1；既有 test 全绿 |

## 回滚

revert compose / nginx.conf / .env.example 改动即可，无应用代码耦合。
