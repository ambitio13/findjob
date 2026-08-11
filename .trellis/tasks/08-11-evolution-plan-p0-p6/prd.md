# 求职智能体演进计划 P0-P6 实施与验收

## Goal

按《求职智能体演进计划》完整实现 P0–P6 七个阶段，将系统从"用户点击触发的流水线"
升级为具备安全护栏、结果度量、针对性内容生成、风险透视、平台解耦、自主反思循环与
评测回归保障的完整 agent，并通过独立审查验收。

价值公式：`面试数 = 找岗 × 匹配 × HR回复 × 面试通过`。本次改动补齐的是
"漏斗没有度量、核心价值（针对性内容）缺位、agent 没有自主性"三大缺口。

## Requirements

### R1. P0 最小安全护栏

- 真实认证（JWT + 登录/注册）替换 `X-User-Id` 信任头；prod 强制 401，demo 降级仅限非 prod
- Redis 令牌桶全局限流中间件，保护模型调用入口与 userscript bridge
- userscript bridge 端点加 channel token，拒绝无 token 的指令拉取/回传

### R2. P1 结果回流闭环

- `application_outcome` 模型 + 迁移：application_id / outcome_type(replied|rejected|interview|offer) / occurred_at / source(manual|userscript_observed) / 证据摘要
- userscript 只读会话扫描指令：仅回传脱敏状态变化，不回传聊天原文
- 前端 application 结果标记入口（一键：已回复/被拒/约面）
- `/metrics` 漏斗面板：投递数、回复率、约面率，按开场白 prompt 版本与 match score 分桶

### R3. P2 JD 针对性简历生成

- `resume_fact_executor` → JD 要求 diff → `targeted_resume` artifact（复用 AgentRun/Artifact 持久化）
- 硬约束：每条改写必须能溯源到 resume fact，溯源失败即拒绝生成（防幻觉/防简历造假）
- 前端投递详情中预览 + 一键复制
- prompt 版本化入库（复用 `generated_artifact.prompt_version`）

### R4. P3 岗位风险透视

- `jd_analysis_executor` 输出扩展：薪资结构化解析、红旗标记（培训贷、岗前培训费、外包驻场、薪资虚高、常年挂单）、公司稳定性信号
- 前端岗位风险面板，结论附 JD 原文证据片段
- JD 全文（`jd_raw`）不得明文落库：以 `enc1$` Fernet 信封加密存储，存量明文由
  迁移 0010 清洗；前端展示通过 ORM 透明解密读取，数据库 raw SQL 只能看到密文信封

### R5. P4 平台解耦与人工投递路径

- "生成-复制"模式：生成开场白+针对性简历 → 一键复制 → 用户自行粘贴，完全不依赖 bridge
- `platforms/manual` 适配器作为一等公民
- 岗位来源 URL 结构化记录（多平台 JD 录入解析的第一步）

### R6. P5 真正的 agent 循环

- arq cron 每日扫描 active application，产出"今日跟进建议"（3 天未回复→换开场白；已回复→技能补齐；低回复率方向→方向策略）
- `OutcomeReflector` 消费 outcome 数据，统计失败模式（按 direction 分组回复率）
- 跟进建议永不直接触发外部动作，采纳走既有 approval boundary
- 用 outcome 数据以统计方法（p25 分位 + clamp）校准 match 阈值，不引入模型训练

### R7. P6 评测体系

- golden set：安全门用例（原始模型输出 → 期望门控决策）+ JD 红旗标注用例
- CI 可跑的 prompt 回归：离线纯函数执行（无网络、无模型），行为变更即失败
- 不变量测试：非 communicate 决策永不升级为 communicate
- 语料规模守卫：防止评测集被意外清空

## Acceptance Criteria

- [x] 后端全量测试通过（真实 PostgreSQL），ruff 无告警
- [x] 前端 lint / tsc / build 全绿
- [x] alembic 单一 head，迁移可干净升级（含双分支 merge）
- [x] docker compose 部署后 health / userscript heartbeat 正常
- [x] 浏览器功能验证：跟进建议面板渲染、手动扫描 200、控制台无报错
- [x] golden 回归全部通过（19 安全门用例 ×2 + 2 守卫 = 40 tests）
- [x] 独立审查智能体按 `review-prompt.md` 完成对抗性验收并出具结论；
  复审口径为 JD 全文不得**明文**落库，允许 `EncryptedText`/`enc1$` 加密落库

## Notes

- 实施与审查整改已完成，当前复审结论为：接受 JD 全文加密落库口径后，P0-P6 可归档。
- 演进计划原文见会话计划文件（P0–P6 七阶段 + "明确不做的事"取舍表）。
- 明确不做：第二家平台自动化、拟人化军备竞赛、模型微调/RAG、多智能体。
