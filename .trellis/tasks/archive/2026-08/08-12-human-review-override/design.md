# Design — needs_review 人工审阅覆写路径

> 对应 PRD R1–R4。安全门（apply_match_safety_gate）一字不动；新增的是
> 门外的**人显式路径**。三层安全保持：确定性门拦自动化 → 人显式担责覆写
> → 审批边界约束执行。

## 1. 后端 prepare 覆写（R1）

### Schema（backend/app/schemas/boss_communicate.py）

```python
class HumanReviewOverride(BaseModel):
    opening_message: str = Field(min_length=1)
    acknowledged: Literal[True]   # 字面 true，前端勾选框映射
    draft_source: Literal["model_draft", "human_written"] = "human_written"

class CommunicatePrepareRequest(BaseModel):
    resume_version_id: str = Field(min_length=1)
    match_artifact_id: str = Field(min_length=1)
    human_review: HumanReviewOverride | None = None   # 新增，可选
```

### Service（boss_communicate_service.prepare_communicate_action）

新增关键字参 `human_review: HumanReviewOverride | None = None`，在解析
产物后的决策断言处分支：

- `human_review is None`：**现状路径不变**（decision 必须 communicate +
  产物自带 opening_message + 复验）。
- `human_review` 存在：
  1. `decision is not needs_review` → 422 `human_review_not_applicable`
     （communicate 无需覆写；skip 明确不匹配，禁止覆写——同一错误码，
     detail 区分文案）；
  2. `validate_opening_message(human_review.opening_message)` 必须通过，
     失败 422 带原因（长度/PII/标点复验，兜底人工输入）；
  3. `outgoing_text = cleaned`（复验后的规范化文本）。

后续 preview/payload_hash/idempotency 逻辑不变——人工消息自然进入
`payload_preview.outgoing_text` 并参与 hash，approve 绑定的是人审后的
确切文本。

### 审计留痕

`build_source_snapshot(...)` 返回 dict 后追加顶层键：

```python
snapshot["human_review"] = {
    "acknowledged": True,
    "acknowledged_at": <iso>,
    "actor_user_id": current_user.id,
    "draft_source": "model_draft" | "human_written",  # 前端传入，见 §3
}
```

取舍：放 `source_snapshot` 而非新列——该列即溯源元数据（JSON），且
陈旧检测只比对快照内 `source_hash` 值，顶层附加键不影响（AC1 验证）。
不新增 alembic 迁移。

### 路由

`api/v1` 的 prepare 路由透传 `body.human_review`；无其他改动。

## 2. match 响应门前草稿（R2）

`_execute_boss_match` 在 gating 处同时持有 `model_output`（门前）与
`gated_output`（门后）。改返回三元组
`(execution, safety_downgraded, raw_opening_message)`：

- `raw_opening_message = model_output.opening_message if safety_downgraded else None`。
- `BossMatchExecution.output` 仍为门后输出；产物 content 仍只存门后
  JSON（不变，隐私/不变量无新增暴露——草稿同时经前端回传并复验）。
- 路由构造 `MatchDecisionOut` 时填新字段 `draft_opening_message`
  （schema 加可选字段，老客户端忽略，向后兼容）。

## 3. 前端人工审阅区（R3）

`client.ts`：`prepareCommunicate(..., humanReview?)` 可选第四参；types
增加 `draft_opening_message` 与 `HumanReviewOverride`。

`RecommendedJobPilotPanel.tsx` needs_review 拦截块内追加：

- 可展开区（默认收起，避免与"放弃此职位"出路抢注意力）：
  - `Input.TextArea` 预填 `matchResult.draft_opening_message ?? ""`，
    标注"模型草稿仅供参考，发送内容以审批预览为准"；
  - `Checkbox`："我已审阅上述风险与缺失要求，确认以本人名义继续准备
    沟通，并对该决定负责"；
  - 按钮「人工审阅后继续」：`disabled = !ack || !msg.trim() || busy`；
    调用 `prepareCommunicate(jobId, resumeVersionId, artifactId,
    { opening_message: msg, acknowledged: true, draft_source })`，
    `draft_source` = 预填草稿未改 → "model_draft"，否则 "human_written"；
  - 成功 → `setPrepareResult` + `setStep("approve")`，与 communicate
    路径汇合。
- **semiAuto 开启时不渲染人审区**（自动路径只认 communicate，AC4）。
- 错误统一 `apiErrorMessage`。

## 4. 不变量保护与测试（R4）

- 门函数、golden 标注、半自动停止规则、execute 需 approve：零改动。
- 后端测试（test_boss_communicate 相关模块）：
  - 覆写正向：needs_review + 有效消息 → action 创建，outgoing_text 为
    人工消息，source_snapshot.human_review 齐全；
  - 422 矩阵：communicate+覆写、skip+覆写、消息过短/PII/标点；
  - 回归：无覆写字段时三种 decision 行为与现状一致（既有测试已覆盖，
    保持全绿）。
- 前端：lint/type-check/build。
- 收尾用 trellis-update-spec 在 evolution-contracts 增补「Human Review
  Override」契约节：仅 needs_review 可覆写、skip 禁止、消息复验、approve
 仍必需、审计键必备。

## 5. 验证设计

- 后端：`pytest -q` + `ruff check .`（含 golden 回归）。
- 前端：`pnpm lint && pnpm type-check && pnpm build`。
- 真实链路：用户用一条 needs_review 职位走 人审→prepare→approve→
  execute 前置（AC7，dry-run 现场）。
