# 求职智能体演进计划 P0-P6 Execution Record

## Status

实施与审查整改已完成（后端 956 passed / ruff 全绿 / 前端 lint+tsc 全绿 /
alembic 单一 head 0010）。独立审查初验为**不通过**（2 阻断 + 1 重要 + 2 建议），
整改后复审口径为 JD 全文不得**明文**落库、允许 `EncryptedText` 加密落库；该口径下可归档。

## Delivered By Phase

### P0 安全护栏

- `backend/app/api/v1/auth.py`、`backend/app/core/security.py` — 注册/登录/JWT
- `backend/app/api/deps.py` — Bearer 优先、非 prod 降级、prod 401
- `backend/app/api/rate_limit.py` + `backend/app/main.py` — 令牌桶中间件
- `backend/app/api/v1/userscript_bridge.py` — channel token

### P1 结果回流

- `backend/app/db/models/models.py` + `alembic/versions/0007_application_outcomes.py`
- `backend/app/schemas/outcome.py`、outcome repo/service、`backend/app/api/v1/applications.py`（结果标记端点）
- userscript 只读会话扫描指令（userscript_observed，脱敏状态变化）
- `backend/app/api/v1/metrics.py` + `backend/app/services/metrics_service.py`（漏斗 + prompt 版本/score 分桶）
- 前端结果标记入口 + 指标面板

### P2 针对性简历

- `backend/app/agents/readiness_executor.py`、`backend/app/services/readiness_service.py`（targeted_resume + fact 溯源）
- 前端预览 + 复制

### P3 岗位风险透视

- `backend/app/schemas/jd_analysis.py`（JdRedFlag/RedFlagType/薪资结构）
- `backend/app/agents/jd_analysis_executor.py`、前端风险面板

### P4 平台解耦

- `backend/app/platforms/manual/` — manual 适配器
- `alembic/versions/0007_job_source_url.py` — 岗位来源 URL
- 生成-复制全链路

### P5 agent 循环

- `backend/app/services/followup_service.py` — 规则 A/B/C + 校准 + 扫描
- `backend/app/agents/reflector.py` — OutcomeReflector
- `backend/app/db/repositories/follow_up_suggestion_repo.py`、`threshold_calibration_repo.py`
- `backend/app/schemas/followup.py`、`backend/alembic/versions/0009_followup_calibration.py`
- `backend/app/queue/worker.py` + `handlers.py` — daily_followup_scan cron（09:00）
- `backend/app/agents/opening_message_guard.py` — min_score 参数化
- `backend/app/services/boss_match_service.py` — 注入校准阈值
- `frontend/src/features/applications/FollowUpSuggestionsPanel.tsx` + API client

### P6 评测体系

- `backend/app/tests/golden/prompt_regression_cases.json`（19 用例）
- `backend/app/tests/golden/jd_red_flag_cases.json`（5 用例）
- `backend/app/tests/test_prompt_regression.py`（40 tests）

## Phase Review Remediation（审查整改，2026-08-11）

### 阻断1：安全门 communicate + None 开场白不降级

- `backend/app/agents/opening_message_guard.py` — communicate 决策开场白为
  None/空/空白/无效时一律降级 needs_review
- golden 用例 `communicate_without_message_passes` →
  `communicate_without_message_downgraded`（expected 翻转为 needs_review）
- `test_prompt_regression.py` — gate 用例改为断言 opening_message **内容**
  （expected 文本或 strip 后输入），不再只断言存在性
- `test_boss_match_executor.py` — None 开场白单测期望同步翻转

### 阻断2：jd_raw 明文落库 → 加密落库

- `backend/app/core/content_crypto.py` — `enc1$<fernet-token>` 信封；密钥 =
  SHA-256(`AUTH_SECRET_KEY`)；prod 缺密钥抛 RuntimeError，非 prod 用固定 dev
  回退键；legacy 明文直通；损坏信封坍缩为空串
- `backend/app/db/types.py` — `EncryptedText` TypeDecorator（钩子
  `process_bind_param` / `process_result_value`）
- `backend/app/db/models/models.py` — `jd_raw` 列改为 EncryptedText
- `backend/alembic/versions/0010_encrypt_jd_raw.py` — 数据迁移：分批加密存量
  明文行，downgrade 解密回明文
- `backend/pyproject.toml` — 新增 `cryptography>=43`
- `backend/app/tests/test_content_crypto.py` — 8 个测试（信封单测 + API 落库
  密文/响应明文端到端）

### 重要：/probe 隐私面收窄

- `backend/app/api/v1/userscript_bridge.py` — prod 环境 403
  `probe_disabled_in_prod`；非 prod 限 op 白名单（count/check_visible/
  read_title/read_url/read_jd/probe_elements），白名单外 400
  `probe_op_not_allowed`（read_content 等读页面内容 op 被封禁）

### 建议：校准极值测试

- `test_followup_and_calibration.py` — 新增 ceiling clamp（replied 95/100 →
  0.8）、replied 组为空 → None 保持默认、replied 得 0 分 → floor 0.4

## Validation Commands

```bash
# 后端（真实 PostgreSQL）
cd backend && set -a && source .env.test && set +a && export QUEUE_NAMESPACE=job-search-agent-test
.venv/bin/python -m pytest -q                          # 预期 956 passed
.venv/bin/python -m pytest app/tests/test_prompt_regression.py -q   # 预期 40 passed
.venv/bin/python -m pytest app/tests/test_content_crypto.py -q      # 预期 8 passed
.venv/bin/ruff check app alembic                       # 预期 All checks passed

# 前端
cd frontend && pnpm lint && pnpm exec tsc -b --force && pnpm build

# 部署（注意：shell 若 source 过 .env.test，必须 env -u 隔离，防止 localhost 值注入容器）
env -u DATABASE_URL -u REDIS_URL -u APP_ENV -u QUEUE_NAMESPACE -u MODEL_PROVIDER \
  docker compose up -d --force-recreate backend worker frontend
curl -sS http://localhost:8000/api/v1/health

# alembic
cd backend && .venv/bin/alembic heads                  # 预期单一 head: 0010_encrypt_jd_raw
# 存量库升级：alembic upgrade head（0010 会把存量明文 jd_raw 加密；需 AUTH_SECRET_KEY 与线上一致）
```

## Known Gotchas（实施中踩过、审查需留意的坑）

1. `JobAnalysis.match_score` 量纲为 **0–100**（非 0–1），校准除以 100 后再 clamp。
2. `SubmitResult` / `CommunicationExecuteResult` 的 `occurred_at` 必填。
3. conftest 的 client fixture 用 TRUNCATE 列表清理，新表必须加入该列表。
4. API 路由顺序：`/applications/follow-up-suggestions*` 静态段必须在 `/{application_id}` 之前注册。
5. alembic 双分支需经 0008 no-op merge 才到 0009，再链到 0010 加密存量
   `jd_raw`；merge 文件不得有多余 import。
6. docker compose `${VAR:-默认}` 插值会被 shell 已 source 的环境变量污染。
7. SQLAlchemy `TypeDecorator` 钩子名是 `process_bind_param` /
   `process_result_value`；写错名字不报错但 processor 静默失效（加密单边
   失效的根因），新自定义类型须先验证 `bind_processor` / `result_processor`
   均非 None。
8. `.env.test` 无 `AUTH_SECRET_KEY`：内容加密模块在非 prod 用固定 dev 回退键，
   prod 必须显式配置且与迁移清洗时一致。
9. `jd_raw` 只能走 ORM 读写（透明加解密）；raw SQL 写入会存明文。
