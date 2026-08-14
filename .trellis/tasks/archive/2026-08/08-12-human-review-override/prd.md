# needs_review 人工审阅覆写路径

## Background（起因）

真实 BOSS dry-run 现场暴露：模型对真实 JD 几乎总会列出若干
`missing_requirements`（简历缺口），而安全门规则
"missing_requirements 非空即降级"使 `needs_review` 从"偶发边界"变成
"默认结果"——用户测试"全是被拦截的"，dry-run 门槛无法积累。

`needs_review` 的语义本是"人工审阅后决定"：半自动 loop 停在它前面等
人。但实现上它是**死胡同**——安全门降级时清空 `opening_message`，
`prepare` 硬性要求 `communicate`（422），人审完后没有任何按钮可继续。
上一任务（08-11-apply-page-ux-and-gate-alignment）把"编辑回写"划入
Out of Scope 时未补这条人审路径，现作为独立任务补齐。

根因观察（记录，不在本任务解决）：missing_requirements 降级规则的
校准问题（模型把"简历缺口"当"关键字段缺失"）涉及安全姿态与 golden
set，另行评估；本任务只补"人审可继续"的路径，安全门本身不动。

## Goal

在安全门不变（只降级不升级、golden set 不动）的前提下，为
`needs_review` 增加一条**显式、审计留痕、仍需审批**的人工覆写路径，
使 dry-run 与日常使用中人审后可以继续 prepare→approve→execute。

## Requirements

### R1. 后端 prepare 接受人工覆写

- `POST /boss/recommended-jobs/{job_id}/communicate/prepare` 请求体新增
  可选 `human_review: { opening_message: str, acknowledged: true }`。
- 仅当产物 decision 为 `needs_review` 时允许；`communicate`（无需覆写）
  与 `skip`（明确不匹配，禁止覆写）携带该字段一律 422。
- 人工消息必须通过 `validate_opening_message`（长度/PII/标点），失败 422
  带原因。
- 覆写时 action 的 `outgoing_text` 取人工消息，正常参与 `payload_hash`；
  approve→execute 流程不变。
- 审计：action 的 `source_snapshot` 增加 `human_review` 键
  （acknowledged/时间/草稿来源），不影响 source_hash 陈旧检测。

### R2. match 响应返回门前草稿

- `MatchDecisionOut` 新增 `draft_opening_message: str | None`：仅当安全门
  降级且门前原始消息非空时返回，供人审参考编辑；持久化产物仍只存门后
  输出（不变）。

### R3. 前端人工审阅区

- needs_review 拦截块内增加可展开"人工审阅"区：草稿 textarea（预填
  draft_opening_message，可编辑）、风险确认勾选（明确担责文案）、
  "人工审阅后继续"按钮调用带 `human_review` 的 prepare。
- 成功后进入与 communicate 相同的 approve 步骤。
- 半自动 loop 永远不发送 `human_review`（自动路径仍只认 communicate）。

### R4. 不变量与回归

- 安全门函数、golden set、半自动 loop 停止规则、execute 需 approve——
  全部不变。
- 既有 communicate 路径与无覆写 prepare 行为不变（回归测试）。

## Acceptance Criteria

- [x] AC1：needs_review + 有效人工消息 + acknowledged → prepare 成功，
      action.outgoing_text 为人工消息，source_snapshot 含 human_review 审计键。
- [x] AC2：decision 为 communicate 或 skip 时携带 human_review → 422；
      消息不合规（过短/PII/标点）→ 422 带原因。
- [x] AC3：match 降级响应含 draft_opening_message（门前草稿）；未降级或
      门前无消息时为 null。
- [x] AC4：前端拦截块可展开人审区，预填草稿、勾选担责、提交后进入
      approve 步骤；semi-auto 模式不出现人审入口。
- [x] AC5：无 human_review 字段的 prepare 行为与现状完全一致；golden set
      与既有测试全绿（`pytest -q` + `ruff check`）。
- [x] AC6：前端 `pnpm lint && pnpm type-check && pnpm build` 全绿。
- [x] AC7：真实 BOSS 页用一条 needs_review 职位走通人审→prepare→approve
      →execute 前置（用户 dry-run 现场验证）。（2026-08-14 归档时确认：人审覆写
      路径已上线，dry-run 记录 1 完成真实发送，用户确认视为满足）

## Constraints

- 不新增 alembic 迁移（复用 JSON 列）。
- 不改 `apply_match_safety_gate` 语义；不改 golden 标注。
- 覆写是**人显式动作**：需请求字段 + acknowledged 字面 true + 后续 approve
  三重确认；任何自动路径不得触发。

## Out of Scope

- missing_requirements 门规则校准 / 提示词调整（另立评估）。
- 覆写次数限制、统计看板（可后续基于审计键做）。
