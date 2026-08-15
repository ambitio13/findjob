# Design：反代真实 IP 信任链与限流防伪造

## 0. 边界

- **改动面**：`backend/Dockerfile` CMD 或 compose 的 backend `command`
  （uvicorn 启动参数）、`docker-compose.yml`（frontend 静态 IP + backend
  环境变量）、`frontend/nginx.conf`（核对现有头，预计零改动或仅注释）、
  `.env.example` / `.env.prod.example`（08-11 侧文件，留衔接说明）。
- **不改动**：`RateLimitMiddleware` 代码与桶分类逻辑、应用层任何 Python
  代码（信任解析完全由 uvicorn 的 ProxyHeadersMiddleware 承担）。

## 1. 核心决策：uvicorn 原生信任链，应用代码零改动

真实 IP 解析**不在应用层做**（不引入 `uvicorn` 之外的第三方 XFF 解析库，
不自己读 `X-Real-IP`）。理由：uvicorn 自带 ProxyHeadersMiddleware，
`--proxy-headers` + `--forwarded-allow-ips` 的语义（从 XFF 右端向左跳过
可信代理，取第一个不可信 IP 作为 `request.client.host`）正是需要的，
且失败模式简单（不信任 → 忽略头）。

## 2. 信任拓扑

```
访客 (真实 IP: R)
  │  可能自带伪造 X-Forwarded-For: F1, F2...
  ▼
nginx (frontend 容器, 静态 IP: 172.28.0.10)   ← 唯一可信代理
  │  X-Forwarded-For: $proxy_add_x_forwarded_for  → "F1, F2, R"
  │  X-Real-IP: $remote_addr → R
  ▼
uvicorn (--forwarded-allow-ips=172.28.0.10)
  从右向左扫描 XFF：R 不可信 → request.client.host = R ✅
  （伪造的 F1/F2 留在列表左侧，永远不会被选中）
```

直连 backend:8000 的请求（来源 IP 不是 172.28.0.10）→ 头整体不被信任，
`request.client.host` = 直连来源 IP（AC2 语义）。

## 3. 具体改动

1. **compose：专属网络 + frontend 固定 IP**。新建 `appnet` 子网：

   ```yaml
   networks:
     appnet:
       ipam:
         config:
           - subnet: 172.28.0.0/24

   services:
     postgres:
       networks: [appnet]
     redis:
       networks: [appnet]
     backend:
       networks: [appnet]
     worker:
       networks: [appnet]
     frontend:
       networks:
         appnet:
           ipv4_address: 172.28.0.10
   ```

   **关键坑（compose 规则）**：服务一旦显式声明 `networks`，就**不再接入
   默认网络**——必须把 postgres/redis/backend/worker/frontend 全部接入
   appnet，否则服务发现（`postgres:5432` 等 DNS 名）断裂，整栈启不来。
   选静态 IP 而非信任整个 /24：uvicorn `--forwarded-allow-ips` 语义以
   主机列表为主，单 IP 最不易踩版本差异；宁可后续扩容时显式加。
2. **backend 启动参数**：compose 的 backend `command` 覆盖 Dockerfile CMD：

   ```yaml
   command: >
     sh -c "alembic upgrade head &&
     uvicorn app.main:app --host 0.0.0.0 --port 8000
     --proxy-headers --forwarded-allow-ips=$${FORWARDED_ALLOW_IPS:-127.0.0.1}"
   ```

   `FORWARDED_ALLOW_IPS` 经 backend `environment` 注入，prod 编排设为
   `172.28.0.10`；本地直连（不经 compose 的 backend command）默认
   `127.0.0.1` 行为不变。
3. **nginx 核对**（预计零改动）：现配置已是
   `X-Forwarded-For $proxy_add_x_forwarded_for` + `X-Real-IP $remote_addr`，
   满足拓扑要求；仅补注释"勿改为直接透传客户端头"。
4. **防呆**：`FORWARDED_ALLOW_IPS=*` 在 `.env.prod.example` 注释中显式
   禁止，说明后果（任意伪造 IP 绕过全部 per-IP 限流）。

## 4. 否决的替代方案

| 方案 | 否决理由 |
|---|---|
| `FORWARDED_ALLOW_IPS=*` | 一行配置就废掉全部 per-IP 限流（正是本任务要堵的洞） |
| 应用层自解析 XFF（中间件读头） | 重造 uvicorn 轮子；可信代理判断逻辑易写错且难测 |
| 用 `X-Real-IP` 做限流身份 | 该头与 XFF 同为可伪造面，且 uvicorn 原生不消费它，需自写解析 → 同上 |
| 信任整个 docker 子网 /24 | 网段内任何被攻破的容器可伪造；收敛到单 IP 成本为零 |

## 5. 测试与验收方式（重要：uvicorn 中间件在 app 之外）

ProxyHeadersMiddleware 是 **uvicorn 服务器层**的中间件，TestClient/
ASGI transport 下不存在——AC1/AC2 无法用单元测试覆盖。分层处理：

- **单测层**：不变（限流 key 生成逻辑已有测试，不依赖 IP 来源）。
- **配置断言层**（可自动化，进 CI）：小脚本断言 compose 解析结果中
  backend command 含 `--proxy-headers` 且 allow-ips 非 `*`、frontend
  固定 IP 存在、nginx.conf 含 `$proxy_add_x_forwarded_for`。
- **e2e 手工层**（AC1/AC2/AC3）：runbook 步骤——compose 拉起后看后端
  `rate_limit.*` 日志的 key 是否为真实 IP；`curl -H
  "X-Forwarded-For: 1.2.3.4" http://backend:8000/api/v1/...` 直连验证
  身份不变。写进 implement.md 的验收命令。

## 6. 兼容性与回滚

- 本地 dev（不经 compose command）：uvicorn 默认 `127.0.0.1` 信任 +
  直连，行为与现状一致（AC4）。
- prod 编排（08-11 R3 的 compose 变体）必须复制同一 command 与静态 IP
  拓扑——**写进父任务的衔接声明，08-11 design 相应段落由父任务收口时同步**。
- 回滚：revert compose/Dockerfile 改动即可，无应用代码耦合。
