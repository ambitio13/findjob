# Showcase Demo Deployment Runbook

> 面试官可访问的公网演示环境部署手册。覆盖 VPS 准备、DNS、部署、验收、
> 日常运维。逐步对齐 PRD AC1–AC12。

## 0. 前置条件（R8 基础设施清单 — 用户操作）

以下均为用户侧操作，提前于代码开发完成可与开发并行。逐项勾选：

- [ ] **VPS**：购买 2C4G 起步的 Linux VPS（Ubuntu 22.04+ 推荐），可安装 Docker。
- [ ] **域名**：购买域名，添加 A 记录指向 VPS 公网 IP。
  - 验收：`dig +short <域名>` 返回 VPS 公网 IP。
- [ ] **Docker**：VPS 安装 Docker Engine + compose 插件（v2）。
  - 验收：`docker compose version` 输出 v2.x。
- [ ] **防火墙**：开放 80/443 入站，关闭其余入站端口。
  - 验收：`nmap -p- <VPS_IP>` 仅见 80/443。
- [ ] **模型服务商账号**：注册 DeepSeek（或 OpenAI 兼容服务商），获取 API Key，
  并在控制台设置用量上限（见 §5）。

## 1. 部署步骤

### 1.1 克隆代码并切到发布 tag

```bash
git clone <repo-url> /opt/tou_jianli_agent
cd /opt/tou_jianli_agent
git checkout showcase-v1   # 部署物 = 该 tag 对应的 commit
```

### 1.2 填写生产环境变量

```bash
cp .env.prod.example .env.prod
vim .env.prod
```

**逐项填写**（参考 `.env.prod.example` 注释中的生成命令）：

| 变量 | 生成方式 | 说明 |
|---|---|---|
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` | 数据库强密码 |
| `DATABASE_URL` | 用上面的密码替换 | 指向 compose 内 postgres:5432 |
| `AUTH_SECRET_KEY` | `openssl rand -hex 32` | 签发 token 的密钥，绝不入库 |
| `AUTH_INVITE_CODE` | `openssl rand -hex 8` | 面试官注册邀请码 |
| `CONTENT_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Fernet 密钥，丢失则 enc2$ 内容不可恢复 |
| `FORWARDED_ALLOW_IPS` | 固定 `172.28.0.10` | **禁止用 `*`**，否则限流失效 |
| `MODEL_API_KEY` | 服务商控制台获取 | 真实 LLM Key，绝不入库 |
| `VITE_DEMO_PASSWORD` | 与 `seed_demo.py` 中一致（默认 `demo12345`） | 公开演示密码，非密钥 |

### 1.3 配置域名

```bash
cp Caddyfile Caddyfile.local  # 不改仓库文件
sed -i 's/SHOWCASE_DOMAIN/<你的域名>/g' Caddyfile.local
# 或直接编辑 Caddyfile 替换 SHOWCASE_DOMAIN
vim Caddyfile
```

> 替换 `Caddyfile` 中的 `SHOWCASE_DOMAIN` 为你的真实域名（如 `demo.example.com`）。
> Caddy 会自动申请并续期 Let's Encrypt 证书。

### 1.4 拉起服务

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

首次构建约 5–10 分钟。Caddy 首次启动会自动申请 TLS 证书（需域名 DNS 已生效）。

### 1.5 初始化演示数据

```bash
# 进入 backend 容器执行种子脚本
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
    python scripts/seed_demo.py --i-know-this-is-demo
```

脚本创建演示账号（`demo` / `demo12345`）、3 条预置 JD、示例简历、match 决策、
投递记录与漏斗指标。幂等——重复执行不会产生重复数据。

## 2. 验收清单（对齐 PRD AC1–AC12）

部署完成后逐项验证：

### AC1：登录页 + 种子数据可见

- [ ] 浏览器打开 `https://<域名>` → 可见登录页。
- [ ] 用 `demo / demo12345` 登录成功。
- [ ] 职位列表有 3 条预置 JD；简历页有示例简历；投递页有 2 条投递记录；
      指标页有漏斗数据。

### AC2：鉴权拦截

- [ ] 退出登录后访问任何业务页面 → 重定向到 `/login`。
- [ ] 未带 token 直接调 API：
  ```bash
  curl -s https://<域名>/api/v1/jobs | head -5
  # 预期 401
  ```

### AC3：邀请码生效

- [ ] 无邀请码注册被拒绝（400）：
  ```bash
  curl -s -X POST https://<域名>/api/v1/auth/register \
    -H 'Content-Type: application/json' \
    -d '{"username":"test_no_invite","password":"test12345"}'
  # 预期 400，reason: invite_code_required
  ```
- [ ] 带正确邀请码注册成功（用你设置的 `AUTH_INVITE_CODE`）。

### AC4：端口收敛 + HTTPS

- [ ] `nmap -p- <VPS_IP>` 仅见 80/443。
- [ ] `curl -vI https://<域名>` 证书有效（Let's Encrypt）。

### AC5：真实 IP 限流

- [ ] 从两个不同公网 IP 分别快速请求 model 桶接口（如 JD 分析），各自独立触发 429。
  ```bash
  # 在两台不同机器上分别执行（需先登录获取 token）
  for i in $(seq 15); do
    curl -s -o /dev/null -w "%{http_code}\n" \
      -H "Authorization: Bearer <token>" \
      https://<域名>/api/v1/jobs/<job_id>/analyze
  done
  # 预期：第 11 次起返回 429（rate_limit_model_per_minute=10）
  ```

### AC6：fake adapter 审批闭环

- [ ] 走通 match → prepare → approve → execute，execute 返回 submitted。
  ```bash
  # 1. 匹配预置 JD
  curl -X POST https://<域名>/api/v1/boss/recommended-jobs/<job_id>/match \
    -H "Authorization: Bearer <token>"
  # 2. 准备投递
  curl -X POST https://<域名>/api/v1/applications/<app_id>/prepare \
    -H "Authorization: Bearer <token>"
  # 3. 审批
  curl -X POST https://<域名>/api/v1/applications/<app_id>/approve \
    -H "Authorization: Bearer <token>"
  # 4. 执行（fake adapter 返回 succeeded）
  curl -X POST https://<域名>/api/v1/applications/<app_id>/execute \
    -H "Authorization: Bearer <token>"
  ```
  - 预期：execute 返回 `submitted`，时间线事件完整。

### AC7：一键重置

- [ ] 污染数据后执行重置，环境回到种子态：
  ```bash
  docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
      python scripts/reset_demo.py --i-know-this-is-demo
  ```
  - 重置后重新登录，数据与首次播种一致（AC1 复验）。

### AC8：CI 流水线绿

- [ ] GitHub Actions CI 在合入前全绿（后端 pytest + ruff，前端 lint + type-check + build）。

### AC9：README 叙事

- [ ] README 包含架构图（mermaid）、三条安全不变量、面试官演示脚本。

### AC10：部署物可追溯

- [ ] 线上运行版本对应 git tag `showcase-v1`。
  ```bash
  cd /opt/tou_jianli_agent && git describe --tags
  # 预期：showcase-v1
  ```

### AC11：每日模型熔断

- [ ] 测试环境将 `MODEL_DAILY_CALL_LIMIT` 设为 N，第 N+1 次 LLM 调用返回 429。
  ```bash
  # 查看熔断状态
  curl -s https://<域名>/api/v1/health | python -m json.tool
  # 预期 model_budget_tripped: true（熔断后）
  ```
- [ ] 次日（UTC 午夜后）或 Redis key 过期后自动恢复。
- [ ] runbook 含服务商侧用量上限设置步骤（见 §5）。

### AC12：基础设施就绪

- [ ] 域名解析指向 VPS：`dig +short <域名>` 返回 VPS IP。
- [ ] Caddy 证书有效：`curl -vI https://<域名>` 证书链完整。
- [ ] 本清单所有项已勾选。

## 3. 日常运维

### 查看服务状态

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

### 查看日志

```bash
# 全部
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f --tail=100

# 单个服务
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f worker
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f caddy
```

### 查看健康状态

```bash
curl -s https://<域名>/api/v1/health | python -m json.tool
```

关注字段：
- `status`: `ok` / `degraded`
- `model_budget_tripped`: `true` 表示当日模型预算已耗尽
- 数据库 / Redis 连通性

### 重置演示数据

当访客污染数据后，30 秒内恢复：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec backend \
    python scripts/reset_demo.py --i-know-this-is-demo
```

### 更新部署

```bash
cd /opt/tou_jianli_agent
git pull origin main
git checkout <new-tag>
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

## 4. 已知取舍

1. **容器启动自动 `alembic upgrade head`**：单副本演示环境可接受；多副本需
   改为独立的 migration job。
2. **演示凭据公开**：`demo / demo12345` 故意公开（演示场景可接受），数据由
   重置脚本兜底。`AUTH_SECRET_KEY` / `MODEL_API_KEY` / `AUTH_INVITE_CODE`
   绝不入库、不进前端产物。
3. **fake adapter**：线上不抓 JD，用预置 JD + fake adapter 走通审批闭环。
   真实 userscript 链路仅本地 + 录屏。
4. **Caddy 直接分流**：Caddy 直接将 `/api/*` 代理到 backend:8000，其余到
   frontend:80。frontend 容器内的 nginx 仍保留 `/api/` 代理配置作为备用路径，
   但 prod 流量不经它。

## 5. 模型服务商侧 API Key 用量上限设置（第二道保险）

每日模型预算熔断（`MODEL_DAILY_CALL_LIMIT`）是应用层防线。服务商侧用量上限
是第二道保险——即使应用层熔断被绕过或 Redis 故障 fail-open，服务商侧仍会
硬性截断费用。

### DeepSeek

1. 登录 [DeepSeek 控制台](https://platform.deepseek.com/)。
2. 进入 **API Keys** 页面，找到线上使用的 Key。
3. 进入 **财务 / 用量管理**，设置：
   - **用量告警阈值**：建议设为 `MODEL_DAILY_CALL_LIMIT` 对应费用的 80%。
   - **余额告警**：设为 10 元（余额低于此值邮件通知）。
4. 充值足够余额（演示场景建议 50–100 元，远超演示用量）。
5. 定期检查用量日志，确认无异常调用。

### OpenAI 兼容服务商

1. 登录服务商控制台。
2. 找到 API Key 设置页面。
3. 设置 **月度用量上限（Monthly spending limit）**：建议设为演示场景预估
   月费用的 2–3 倍。
4. 设置 **用量告警**：在用量达到上限的 50% / 80% / 100% 时邮件通知。
5. 如服务商支持按 Key 设置速率上限（RPM / TPM），建议设置与
   `RATE_LIMIT_MODEL_PER_MINUTE` 一致的值。

> 以上步骤确保：即使应用层 Redis 熔断因故障 fail-open，服务商侧仍会
> 在硬性额度处截断，公网真实 Key 的费用有确定性上限。

## 6. 回滚

部署侧回滚 = 停 prod compose + DNS 摘除：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml down
# DNS A 记录删除或指向其他地址
```

代码侧回滚 = 切回上一个 tag：

```bash
cd /opt/tou_jianli_agent
git checkout <previous-tag>
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```
