# BOSS 本地开发 Runbook

本文档是 BOSS 半自动沟通链路（inspect → match → prepare → approval → bridge →
userscript → execute）的本地开发运行手册。覆盖日常 loop、故障定位、日志查看与残留
清理。脚本入口在 `scripts/`，README 只保留短路径。

## 1. Daily Loop

```bash
# 1) 启动全栈（Postgres + Redis + backend + worker + frontend）
docker compose up -d --build

# 2) 健康检查（compose / backend /health / frontend / worker 日志标记）
./scripts/dev-health.sh

# 3) BOSS E2E smoke（bridge round-trip + inspect bounded failure）
./scripts/e2e-smoke.sh --skip-up

# 4) 质量门
cd backend && ./.venv/bin/ruff check app && ./.venv/bin/pytest -q
cd frontend && pnpm lint && pnpm type-check && pnpm build

# 5) 看日志
./scripts/dev-logs.sh
```

> 没有模型 Key 时本地测试照常通过（Fake 提供者，不发起网络请求）。BOSS 真实页面
> 交互需要先在浏览器安装 `docs/boss-userscript.user.js` 并打开 BOSS 标签页。

> **启用 userscript bridge（本地开发）**：compose 默认
> `BOSS_USERSCRIPT_BRIDGE_ENABLED=0`（出于安全考虑，新部署默认不暴露 bridge）。
> 需要本地 bridge 链路时，在启动前导出该变量：
>
> ```bash
> export BOSS_USERSCRIPT_BRIDGE_ENABLED=1
> docker compose up -d --build
> ```
>
> 注意：即便启用，`APP_ENV=prod` 时 bridge 路由也不会注册（见 08-15 暴露面收敛）。

## 2. 故障地图（症状 → 原因 → 证据 → 修复）

### 2.1 backend / worker / Redis / Postgres 未就绪

**症状**：`dev-health.sh` 报 backend 或 worker 不健康；前端 `/api/v1/health` 5xx。

**最可能原因**：compose 服务未起、依赖未就绪、端口冲突、`.env` 缺失。

**证据**：
```bash
docker compose ps                      # 看各服务状态
docker compose logs --tail=100 backend # 看 backend 启动报错
docker compose logs --tail=100 worker  # 看 worker 启动报错
./scripts/dev-health.sh                # 逐步定位
```

**修复**：
- 端口占用：`lsof -i :8000 / :5173 / :5432 / :6379`，释放或改 `.env`。
- DB 迁移缺失：`docker compose exec backend alembic upgrade head`。
- `.env` 缺失：`cp .env.example .env`。
- 彻底重来：`docker compose down -v && docker compose up -d --build`
  （`-v` 会删除数据卷，仅本地开发用）。

### 2.2 userscript bridge 未连接

**症状**：inspect 返回 `read_failed` / `bridge_not_connected`；pilot panel 卡在
“等待 userscript”。

**最可能原因**：userscript 未安装、BOSS 标签页未开、bridge 通道过期。

**证据**：
```bash
curl -s http://localhost:8000/api/v1/userscript-bridge/status | python3 -m json.tool
# connected=false 说明 userscript 未心跳
```

**修复**：
1. 确认 `docs/boss-userscript.user.js` 已装进 Tampermonkey/油猴。
2. 打开 BOSS 推荐职位页（不要打开多个 BOSS 标签页，避免 page_id 漂移）。
3. userscript 会每 5s 心跳；`connected=true` 后再触发 inspect。
4. 若 `connected=true` 但仍失败，看 `boss.bridge.*` 日志（见 §3）。

### 2.3 wrong-tab / page hash 不匹配

**症状**：日志出现 `boss.bridge.instruction_requeued`；prepare/execute 返回
`page_mismatch`。

**最可能原因**：用户开了多个 BOSS 标签页，或当前标签页 URL 与目标 job 不符。

**证据**：
```bash
./scripts/dev-logs.sh | grep -E "page_mismatch|instruction_requeued|expected_url_hash"
curl -s http://localhost:8000/api/v1/userscript-bridge/status | python3 -m json.tool
# 对比 page_url_hash 与目标 job 的 external_id
```

**修复**：
- 关闭多余 BOSS 标签页，只保留目标 job 页。
- 在目标页刷新一次让 userscript 重新心跳。
- 重新触发 inspect / prepare。

### 2.4 inspect 长时间等待或返回 `read_failed`

**症状**：`POST /boss/recommended-jobs/current/inspect` 一直 pending 或返回
`inspect_status=read_failed`。

**最可能原因**：bridge 未连接、userscript 在非目标页、JD 选择器漂移。

**证据**：
```bash
./scripts/dev-logs.sh | grep -E "boss_recommended_job.inspect|boss.bridge"
# 关注 failure_code、page_url_hash、instruction_id
```

**修复**：
- bridge 未连接 → 见 §2.2。
- 页面不符 → 见 §2.3。
- 选择器漂移：在 userscript 控制台手动跑 `read_jd`，看返回结构；若 BOSS 改版需
  更新 `docs/boss-userscript.user.js` 的选择器配置。

### 2.5 worker 出现 `missing_run`

**症状**：worker 日志出现 `queue.*_missing_run`，`agent_run_id=...` 找不到对应行。

**最可能原因**：队列任务入队后 AgentRun 行被删/回滚，或跨环境共享 Redis 导致
namespace 串。

**证据**：
```bash
./scripts/dev-logs.sh | grep -E "missing_run|queue_namespace|workflow_type"
# 确认 queue_namespace 是否为预期的 job-search-agent（本地）或 smoke 前缀
```

**修复**：
- 本地与 smoke 串了：确认 smoke 用独立 `QUEUE_NAMESPACE=job-search-agent-smoke-*`。
- DB 被清：`docker compose exec backend alembic upgrade head` 重建。
- 残留任务：见 §4 清理 smoke 残留（不要清生产 namespace）。

### 2.6 前端 pilot panel 状态与后端不一致

**症状**：前端显示“已完成”但后端 AgentRun 仍 `running`；或前端卡住后端已失败。

**最可能原因**：前端轮询中断、浏览器缓存、后端状态机异常。

**证据**：
```bash
# 后端真实状态
curl -s http://localhost:8000/api/v1/applications | python3 -m json.tool
# 前端控制台看轮询请求与 agent_run_id
```

**修复**：
- 前端硬刷新（Cmd+Shift+R）。
- 对比前端错误面板里的 `agent_run_id` 与后端日志，确认是否同一 run。
- 若后端 AgentRun 卡 `running`：看 worker 日志是否异常退出，必要时手动标记
  `agent_runs.status='failed'`（仅本地）。

## 3. Bridge 日志调试

bridge 链路日志事件（按时间序）：

| 事件 | 关键字段 | 含义 |
| --- | --- | --- |
| `boss.bridge.heartbeat` | `page_id`, `page_url_hash` | userscript 心跳，5s 一次 |
| `boss.bridge.instruction_sent` | `instruction_id`, `op`, `page_id`, `expected_url_hash` | backend 下发指令 |
| `boss.bridge.instruction_requeued` | `instruction_id`, `instruction_page_id`, `requesting_page_id` | 指令属别的标签页，重入队 |
| `boss.bridge.result_received` | `instruction_id`, `success`, `error`(失败时) | userscript 回传结果 |
| `boss.bridge.result_timeout` | `instruction_id`, `op`, `timeout_s` | 90s 无结果，指令超时 |

串日志的钥匙：`instruction_id`（单次往返）+ `page_id`（标签页）+
`page_url_hash`（目标页）。一条失败的 run 应能从 `inspect_ok` → `instruction_sent`
→ `result_received(success=false)` 或 `result_timeout` 串起来。

```bash
# 串一条 run
./scripts/dev-logs.sh | grep -E "<instruction_id 或 agent_run_id>"
```

> 安全：日志只记 `page_url_hash`（哈希），不记原始 URL、cookie、token、raw HTML。

## 4. 残留清理

只允许清理**固定 smoke/test 前缀**的资源。禁止 `flushdb`、宽泛 `delete`、无约束
SQL。

### 4.1 自动清理（脚本）

```bash
./scripts/clean-smoke-artifacts.sh
```

脚本只删：
- Redis 中 `job-search-agent-smoke-*` namespace 的队列键。
- DB 中 `smoke-%` 前缀的 UserProfile / AgentRun 行。

脚本会先打印将删除的目标，要求确认（`--yes` 跳过确认）。

### 4.2 手动清理（需人工确认）

- 生产 namespace `job-search-agent` 的残留：**必须人工核对**，不可脚本删。
- 本地 DB 全清：`docker compose down -v`（删卷，仅本地）。
- resume 临时上传：`backend/data/resumes/` 下的测试文件可手动删。

## 5. 推荐职位发现流水线（Discovery Pilot）

### 5.1 概述

发现流水线在 BOSS 推荐职位页（`/web/geek/jobs`）执行**零导航**串行采集：
扫描可见卡片 → 点击卡片根（右侧 pane 原地切换，不跳转）→ 读取 JD → upsert →
匹配 → 仅准备（prepare-only）。v0.1 **不会自动审批或自动发送**。

- API：`POST /api/v1/boss/recommended-jobs/discovery`
- 参数：`resume_version_id`（必填）、`limit`（默认 3，上限 10）、`mode`（仅
  `prepare_only`；`auto_execute` 在 dry-run gate 未通过时返回 422）。
- 每个职位的结果：`prepared`（communicate → 已创建 approval_required action）、
  `skipped`（含 `skip_reason=already_persisted`）、`needs_review`、`failed`、
  `stopped`。
- 硬停止：连续 3 次 `failed` 或遇到 `unexpected_navigation` / `page_mismatch` /
  `captcha_required` / `rate_limited` 等硬停止码时立即终止，剩余项标记 `stopped`。

### 5.2 运行步骤

1. 确保后端全栈已启动（见 §1）。
2. 打开 BOSS 推荐职位页 `/web/geek/jobs`，确认 userscript 已连接（见 §2.2）。
3. 在前端发现面板选择简历版本，设置 `limit=3`，点击"开始发现"。
4. 观察面板中的逐项进度（`pending → opening → reading → persisted → matching →
   prepared/skipped/needs_review`）。
5. 运行结束后检查结果：`communicate` 的职位会生成 `approval_required` action，
   可在审批页查看。

### 5.3 零导航确认清单

运行期间应确认：

- [ ] 页面 URL 始终保持在 `/web/geek/jobs`，未发生跳转。
- [ ] 没有新标签页或新窗口被打开。
- [ ] 右侧 pane 随每张卡片点击原地切换内容。
- [ ] `communicate` 结果仅创建 `approval_required` action，未发送任何消息。
- [ ] 未提交任何简历（无 auto-submit）。
- [ ] 最多处理 `limit` 个职位（默认 3）。

### 5.4 故障排查

| 症状 | 可能原因 | 修复 |
| --- | --- | --- |
| `status=hard_stopped` + `failure_code=unexpected_navigation` | 页面发生跳转（弹窗/广告/手动操作） | 关闭弹窗，回到推荐列表页，重新运行 |
| `status=hard_stopped` + `failure_code=page_mismatch` | pane 标题与卡片不符 | 刷新页面，确认 userscript 心跳正常后重试 |
| `status=failed` + `failure_code=scan_failed` | bridge 未连接或页面不在推荐列表 | 见 §2.2；确认 URL 是 `/web/geek/jobs` |
| 单项 `failed` + `failure_code=item_error` | JD 太稀疏 / upsert 失败 / 匹配异常 | 查看该项 `message` 字段；若仅个别项可忽略 |
| `bridge_not_connected` (HTTP 400) | userscript 未连接 | 见 §2.2 |

### 5.5 安全边界

- 发现流水线不暴露新的任意 selector/evaluate 接口给前端。
- `scan_visible_jobs` 不扩大生产探针白名单；`open_job_by_key` 不可从诊断探针端点
  调用。
- 禁点：`a.job-name`、`a.more-job-btn`、`.op-btn-chat`、`.op-btn-like`；
  禁采：`.job-boss-info`。
- 不持久化或记录原始 URL、原始 HTML、cookie、token、联系人姓名或聊天内容。

## 6. 相关文档

- 人工试点步骤：`docs/manual-boss-pilot.md`
- 测试路线：`docs/boss-communicate-testing-plan.md`
- E2E smoke 详解：`docs/e2e-smoke.md`
- BOSS 反自动化调研：`docs/boss-anti-automation-findings.md`
- 编码规范：`.trellis/spec/`
