# 投递详情页拆分与匹配门控体验对齐

## Background（起因）

用户在真实 BOSS 链路上手动执行 dry-run 门槛积累（父任务
`08-03-boss-communicate-testing-hardening` 阶段 C）时，暴露了三个阻断
操作流的产品缺陷：

1. **页面结构混乱**：投递详情页把"手动链"（读取 JD → 逐个生成五个就绪材料
   → 手动复制投递）和"半自动 BOSS Pilot"（inspect→match→prepare→approve→
   execute）堆在同一个长列里（12+ 个面板），两种工作流互相干扰，dry-run
   操作者极易点错区域。
2. **needs_review 契约不一致**：匹配结果为 `needs_review` 时，前端 PilotPanel
   推进到准备步骤并提示"确认后点击「准备沟通」继续"，但后端
   `prepare_communicate_action` 硬性要求 decision 为 `communicate`（422
   拒绝）——用户点击后必然报错。根因是安全门降级时会清空 `opening_message`，
   后端拒绝是正确的（spec/backend/evolution-contracts.md §2：只降级不升级），
   错在前端承诺了不允许的操作。
3. **"可编辑开场白"失效承诺**：PilotPanel 的开场白输入框标注"可编辑"，但
   `prepare` API 只传 `match_artifact_id`，后端读取持久化产物原文，用户编辑
   从不生效——编辑框是纯装饰，误导用户以为改动会被发送。

## Goal

在不触碰后端安全不变量的前提下，重构投递详情页信息架构、修复两处前端
契约承诺，使 dry-run 操作者能在清晰的分区里顺畅积累验证记录。

## Requirements

### R1. 投递详情页分区拆分

- 投递详情按工作流拆为 Tabs：材料准备（手动链）/ 审批与执行 / BOSS 自动
  沟通 Pilot（仅 boss 平台职位显示第三个 Tab）。
- 分析、生成材料、外部执行三类内容视觉分离（遵守
  spec/frontend/table-detail-views.md 交互规则）。
- Tab 切换不丢失已进行中的状态（如材料生成轮询）。

### R2. 一键生成全部就绪材料

- 就绪材料面板新增"一键生成全部"：串行入队全部产物类型，复用现有
  autoGenerate 逻辑与轮询机制。
- 保留单类型生成入口；未绑定简历时禁用并给出提示。

### R3. needs_review 门控对齐

- 匹配结果为 `needs_review` 时，前端**禁用**"准备沟通"按钮，展示拦截原因
  （评分低于阈值 / 缺失要求 / 风险项）与明确出路（重新匹配、更换职位、
  调整简历后重试）。
- 移除"确认后点击准备沟通继续"的误导性文案。
- 后端保持现状（422 是正确的确定性护栏），油猴脚本无需改动。

### R4. 开场白展示去误导

- 采用对齐后端不变量的方案：开场白展示为**只读**（含复制），文案注明
  "发送内容以审批预览为准，修改需重新匹配"。
- 不实现用户编辑回写后端（会削弱 payload_hash 审批边界，记录为 Out of
  Scope 的未来增强）。

## Acceptance Criteria

- [x] AC1：投递详情按 Tab 分区展示，三个 Tab 内容归属正确；boss 平台职位
      才有 Pilot Tab；切换 Tab 不中断进行中的材料生成轮询。
      ✅ `ApplicationsPage.tsx` Tabs（materials/approval/pilot），pilot 条件
      渲染 `job?.platform === "boss"`，`destroyInactiveTabPane={false}` 保留
      已挂载面板使轮询跨 Tab 不中断。静态门通过；手动回归待 D1。
- [x] AC2："一键生成全部"串行入队全部产物类型，进度可见，全部完成后各
      类型材料出现在列表中；未绑定简历时按钮禁用。
      ✅ `ArtifactChecklist.tsx` generateAll useCallback 串行 enqueueGeneration
      + pollRunIds 收集；Popconfirm 按钮 disabled=`resumeMissing || generating`。
- [x] AC3：`needs_review` 时"准备沟通"按钮禁用，界面展示拦截原因与出路；
      不再出现"点击准备沟通继续"文案。
      ✅ `RecommendedJobPilotPanel.tsx` PrepareApproveStepCard 渲染条件收窄为
      仅 `communicate`；needs_review Alert(error) 展示 score/risks/missing +
      重新匹配 + 出路指引；旧"点击准备沟通继续"文案已删除。
- [x] AC4：开场白输入框改为只读展示 + 复制；文案不再承诺编辑生效。
      ✅ Typography.Paragraph copyable 只读展示；提示"发送内容以审批预览为准；
      如需调整请重新匹配"；`onOpeningMessageChange` prop 链已移除。
- [x] AC5：`communicate` 决策的完整 Pilot 流程（inspect→match→prepare→
      approve→execute 前置）不受本次改动影响（回归验证）。
      ✅ communicate 路径 setStep("prepare") 不变；PrepareApproveStepCard
      条件仍含 communicate；开场白仅 communicate 时展示。静态门通过；手动
      回归待 D1。
- [x] AC6：`pnpm lint && pnpm type-check && pnpm build` 全部通过。
      ✅ 2026-08-11 三轮验证（Phase A/B/C 各一次）全绿。

## Constraints

- 纯前端任务：不改后端 API 契约、不改油猴脚本、不新增 alembic 迁移。
- 不得削弱安全不变量：安全门只降级不升级；execute 前必须 approve；
  半自动 loop 在有外部副作用的步骤前必须停止。
- 复用现有组件与 hook（enqueueGeneration/轮询、useBridgeStatus、
  apiErrorMessage），不重复造轮子。

## Out of Scope

- 用户编辑开场白并回写后端（需新 API 字段 + payload 重算 + 审批边界评审）。
- 投递记录列表的表格化重构（ProTable）。
- BOSS Pilot 批量模式（属于 `08-03-boss-recommended-list-batch-loop`）。
