# Design — 投递详情页拆分与匹配门控体验对齐

> 纯前端任务。对应 PRD R1–R4。核心原则：后端 422 是正确行为（确定性护栏），
> 修复方向是前端对齐后端契约，而不是放宽后端。

## 1. 投递详情页信息架构（R1）

### 现状

`ApplicationsPage` → `ApplicationDetail` 一个垂直长列堆叠 12+ 面板：
投递信息、ReadinessSummary、SourceSnapshotPanel、ArtifactChecklist、
ManualSubmitPanel、FailurePanel、Timeline、状态操作、OutcomePanel、
GuidedSubmitPanel、RecommendedJobPilotPanel（boss 条件渲染）、
ApplicationActionsPanel。手动链与半自动 Pilot 混杂。

### 目标结构

页面级（ApplicationsPage，不变）：`BossInspectEntry` + `FollowUpSuggestionsPanel`
+ 投递记录选择器。

`ApplicationDetail` 重构为「公共头 + Tabs」：

```
公共头（任何 Tab 都可见，紧凑）：
  投递信息 Card（现状保留）
  ReadinessSummary（就绪概览，跨工作流都需要）
  FailurePanel（仅 latest_error 存在时显示——失败必须始终可见）

Tabs（AntD Tabs，默认 items 行为：惰性首次渲染、切换不销毁已挂载面板）：
  Tab「材料准备」（默认）：
      SourceSnapshotPanel → ArtifactChecklist → ManualSubmitPanel
  Tab「审批与执行」：
      GuidedSubmitPanel → ApplicationActionsPanel → 状态操作 → OutcomePanel
      → TimelinePanel
  Tab「BOSS 自动沟通 Pilot」（仅 job.platform === "boss"）：
      RecommendedJobPilotPanel
```

依据 spec/frontend/table-detail-views.md：分析、生成材料、外部执行视觉分离。

### 关键决策与取舍

- **Tabs 而非新路由**：Pilot 与手动链共享同一 application 上下文（job、
  resume version、artifacts），拆路由会造成状态复制。Tab 状态用
  `useState`，切换 application 时重置为默认 Tab。不做 URL 同步（当前页无
  search-params 约定；刷新回到默认 Tab 可接受——记录为已知取舍）。
- **切换不销毁**：AntD Tabs 默认保留已挂载面板，ArtifactChecklist 的
  agent-run 轮询在 Tab 切换后继续（AC1 验证点）。
- **FailurePanel 放公共头**：失败信号不应藏在某个 Tab 里。
- **Timeline/状态操作/Outcome 归「审批与执行」**：它们是投递生命周期的
  记录与推进，与材料准备无关。

## 2. 一键生成全部（R2）

`ArtifactChecklist.tsx` 已有串行全量生成逻辑（autoGenerate effect，
第 244–268 行），仅在 freshCreate 时自动触发。改造：

- 提取为 `generateAll(silent: boolean)` useCallback：按 `ARTIFACT_TYPES`
  顺序 `enqueueGeneration(type, { silentOn409: true })`，收集 runId 写入
  `pollRunIds`（复用现有轮询与完成提示）。
- autoGenerate effect 改为调用 `generateAll(true)`（行为不变）。
- 工具区新增按钮「一键生成全部」：`Popconfirm` 确认后 `generateAll(false)`；
  `disabled` 条件 = `resumeMissing || generating || pollRunIds.size > 0`
  （有进行中的生成时禁止重入）。
- 单类型生成入口保留不动。

## 3. needs_review 门控对齐（R3）

### 责任归属结论

- **油猴脚本无关**：脚本只执行指令，不参与决策。
- **后端不改**：`prepare_communicate_action` 的 422 是确定性护栏；安全门
  降级时已清空 `opening_message`，放开 prepare 也无消息可发（契约
  §2「只降级不升级」）。
- **前端是唯一错的一方**：承诺了护栏不允许的操作。

### RecommendedJobPilotPanel 修改

1. `handleMatch` 的 needs_review 分支：保留 `setSemiAuto(false)` 自动停止
   （既有行为，符合 spec/frontend/components.md Semi-Auto Loop Safety），
   文案从"请查看风险与缺失项后决定是否继续"改为明确拦截表述。
2. PrepareApproveStepCard 渲染条件（现第 470 行）从
   `communicate || needs_review` 收窄为**仅 `communicate`**——needs_review
   不再出现"准备沟通"按钮。
3. needs_review 时 MatchStepCard 下方新增拦截说明块（`Alert type="error"`）：
   - 标题："安全门拦截：当前不允许准备沟通"；
   - 展示后端已返回的信息：score、risks、missing_requirements（均已在
     matchResult 中，**不从前端重算阈值**——spec 禁止在展示组件复制后端
     决策规则）；
   - 出路按钮：「重新匹配」（重新调用 handleMatch）+ 文字指引（更换职位 /
     调整简历后重新匹配）。
4. MatchStepCard 内原 needs_review Alert（"确认后点击准备沟通继续"）删除，
   由第 3 点的拦截块取代。

## 4. 开场白只读化（R4）

MatchStepCard 的开场白区域（现第 715–722 行）：

- `Input.TextArea` → `Typography.Paragraph`（`copyable`、保留换行的只读
  渲染）；
- 标题从"开场白（可编辑）"改为"开场白"，下方提示："发送内容以审批预览
  为准；如需调整请重新匹配"；
- 移除 `onOpeningMessageChange` prop 链路；`openingMessage` state 保留作
  展示源（match 返回时写入）。

取舍记录（为何不做编辑回写）：prepare 的 payload 来自持久化产物并参与
`payload_hash` / 审批边界（契约 §1）。允许前端编辑回写需要：新 API 字段、
payload 重算、人工内容的安全校验、审批边界语义评审——单独立项，
见 PRD Out of Scope。

## 5. 不变量保护清单

- 半自动 loop：inspect/match/prepare 可自动，approve/execute 永远人工
  （现状已满足，重构不得破坏）；needs_review/skip/任何失败即停。
- execute 前必须 approve（ApplicationActionsPanel 审批流不动）。
- 不复制后端决策规则到前端（只用后端返回的 decision/score/risks 展示）。
- API 错误统一走 `apiErrorMessage`；异步生成沿用 `useAgentRunPolling` /
  既有轮询与 copy 文案助手。

## 6. 验证设计

前端无单测设施，验证 = 静态门 + 手动回归：

- 静态门：`pnpm lint && pnpm type-check && pnpm build`。
- 手动回归清单（本机 dev + 真实 BOSS 页）：
  1. communicate 职位：inspect→match→prepare→approve→execute 前置全链路；
  2. needs_review 职位：确认按钮禁用 + 拦截块 + 重新匹配可用；
  3. 一键生成全部：五类材料串行入队、进度与完成提示、已有进行中任务时
     按钮禁用；
  4. Tab 切换：材料生成轮询跨 Tab 不中断；boss/非 boss 职位 Tab 数量正确。
