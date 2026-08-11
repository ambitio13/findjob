# 求职智能体演进计划 P0-P6 Design

## Purpose

本文档记录 P0–P6 的关键架构决策、数据模型与安全不变量，作为独立审查的对照基准。
实现已完成；审查重点是"实现是否真实满足这些设计意图，且无隐藏违反"。

## Safety Invariants（全局不变量，审查最高优先级）

1. **审批边界**：agent 只能 prepare，一切外部副作用（发消息、投简历、平台操作）必须经
   用户显式 approve。跟进建议（P5）本身永不触发外部动作，采纳建议只改建议行状态。
2. **安全门只降级不升级**：`apply_match_safety_gate` 永不把非 communicate 决策升为
   communicate；低分 / 缺失(None)/空/空白开场白 / PII 命中一律降级。
   （审查整改：原实现遗漏 None 开场白，已修复并翻转 golden 期望。）
3. **用户数据隔离**：所有用户级资源读写必须过所有权校验（JWT subject → auth_users →
   profile）；prod 无 token 即 401。
4. **隐私不变量**：userscript 只读扫描仅回传脱敏状态变化（不回传聊天原文）；JD 全文
   （`jd_raw`）以 `enc1$` Fernet 信封加密落库（`EncryptedText` 列类型，ORM 透明
   加解密；迁移 0010 清洗存量明文）；`/probe` 仅非 prod 可用且限只读 op 白名单；
   日志不含简历原文/凭据。（审查整改：原实现明文落库，已改为加密。）
5. **溯源硬约束**：targeted_resume 每条改写必须能溯源到 resume fact，溯源失败即拒绝生成。

## P0 认证与限流

- `app/api/v1/auth.py` + `app/core/security.py`：注册/登录签发 JWT；
  `deps.get_current_user` 解析顺序 = Bearer token（subject 校验 auth_users 存在性）→
  非 prod 降级（X-User-Id / demo_user_id）→ prod 401。
- `app/api/rate_limit.py`：Redis 令牌桶中间件，`rate_limit_enabled` 可配置。
- `userscript_bridge.py`：channel token（共享密钥 + 时间窗）保护指令拉取/回传。

## P1 结果回流

- `ApplicationOutcome`：application_id / outcome_type(replied|rejected|interview|offer) /
  occurred_at(必填) / source(manual|userscript_observed) / 证据摘要。迁移 0007。
- `/metrics`（`metrics_service.py`）：漏斗指标按 opening_message prompt_version 与
  match score 分桶。
- 完成标志语义：可回答"哪类开场白回复率最高、match score 与回复率是否相关"。

## P2 针对性简历

- 链路：resume facts → JD 要求 diff → `targeted_resume` artifact（AgentRun/Artifact 持久化，
  prompt_version 入库）。
- 溯源校验在生成侧强制执行，前端仅提供预览+复制，不提供编辑后回写绕过路径。

## P3 岗位风险透视

- `JdRedFlag`：flag_type（training_loan / training_fee / outsourcing_onsite /
  inflated_salary / ...）+ severity + evidence_quote（JD 原文片段）。
- golden set 标注词汇必须与 `RedFlagType` Literal 一致（回归测试守卫）。

## P4 平台解耦

- `platforms/manual` 为一等公民适配器；"生成-复制"路径完全不依赖 bridge。
- `job_postings.source_url`（迁移 0007_job_source_url）支撑多平台 JD 录入。

## P5 agent 循环

- 数据模型（迁移 0009）：
  - `FollowUpSuggestion`：type(change_opening_message|skill_gap_plan|low_reply_rate_direction) /
    status(pending|actioned|dismissed)；同 application 同 type 已有 pending 则去重跳过。
  - `ThresholdCalibration`：append-only，最新行生效；method=quantile_p25。
- 规则（`followup_service.py`，纯确定性、可审计）：
  - A：submitted ≥3 天且无 reply 类 outcome → change_opening_message
  - B：有 replied 类 outcome 且无 pending skill_gap_plan → 创建，detail 嵌入最新
    jd_analysis artifact 的 skill_gaps
  - C：OutcomeReflector 判定低回复率 direction（min_samples=5, low_reply_rate=0.10）→
    该 direction 所有 active application 各建一条
- 校准：样本 = 有 outcome 且有最新 match_score 的 application；replied 组 p25（线性插值）
  /100 后 clamp [0.4, 0.8]；样本 <5 或 replied 组空 → 保持默认 0.6。
  注意 JobAnalysis.match_score 量纲为 0–100。
- 调度：arq `cron(daily_followup_scan, hour=9, minute=0)`；`scan_all_users` 每用户独立
  try/except + rollback，单用户失败不拖垮全局。
- resolve 语义：404=不存在/无权限，409=已解决。

## P6 评测体系

- `tests/golden/prompt_regression_cases.json`：19 个安全门用例（model_output +
  expected{decision, opening_message_present, 可选 min_score / opening_message}）。
- `tests/golden/jd_red_flag_cases.json`：5 个红旗标注用例。
- `test_prompt_regression.py`：parametrize 跑纯函数，离线无网络；含"永不升级"不变量、
  标注词汇一致性（`get_args(RedFlagType)`）、语料规模守卫（gate ≥10、red flag ≥4）。

## Alembic 拓扑

0001 → 0005 → {0006_auth_users → 0007_application_outcomes} 与
{0006_job_analysis_red_flags → 0007_job_source_url} 双分支 → 0008_merge_heads（no-op）→
0009_followup_calibration → 0010_encrypt_jd_raw。单一 head = 0010。

## Rejected Alternatives

- 第二家平台自动化：风控结构性风险，ROI 低于"生成解耦 + 手动投递"。
- 模型微调 / RAG：outcome 数据量不足，统计校准先行。
- 多智能体 / subagent：单循环够用，避免过度设计。
- 建议自动执行：违反审批边界不变量，明确拒绝。
