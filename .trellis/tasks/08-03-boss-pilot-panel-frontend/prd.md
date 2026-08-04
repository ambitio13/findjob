# 前端 RecommendedJobPilotPanel 组件

## Goal

实现设计文档（`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`
第 45-51 行）中设计但未实现的 `RecommendedJobPilotPanel` 前端组件。该组件是用户操作
BOSS 自动沟通流程的主要交互界面。

## Confirmed Facts

- 设计文档第 45-51 行定义了组件布局和功能。
- 后端 API 已全部就绪：inspect、match、prepare、approve、execute 端点。
- 前端已有 bridge status 显示的基础设施（`useBridgeStatus` hook，5s 轮询）。
- 当前用户需要通过 `curl` 命令操作整个流程，无 GUI。
- 组件以 `GuidedSubmitPanel.tsx` 为模板（同为 Card + 两阶段审批门控 + bridge 状态）。

## Requirements

### R1. 组件功能

`RecommendedJobPilotPanel` 组件包含以下区域：

1. **Bridge 状态** — 显示油猴脚本连接状态（已连接/未连接）、当前 `page_id`、心跳时间
2. **JD 预览** — 显示 `inspect` 返回的职位信息（标题、公司、薪资、描述等）
3. **匹配决策** — 显示 `match` 的决策结果（`communicate` / `skip` / `needs_review`）
   和决策依据（score、reasons、risks、missing_requirements）
4. **开场白预览** — 显示 `opening_message` 内容，支持人工审阅（后端 prepare 使用
   match artifact 中的存储消息，前端编辑仅作参考）
5. **操作控制** — prepare / approve / execute 按钮，显示当前 action 状态
6. **步骤进度** — 用 `Steps` 组件可视化 inspect→match→prepare→approve→execute 进度

### R2. 交互流程（手动模式，默认）

1. 用户导航到 BOSS 职位详情页 → 点击「开始读取」调用 inspect → 显示 JD
2. 点击「匹配分析」→ 调用 match → 显示决策结果和开场白
3. 用户点击「准备发送」→ 调用 prepare → 显示 `approval_required` 状态
4. 用户审阅后点击「批准」→ 调用 approve → 显示 `approved` 状态
5. 用户点击「执行」→ 调用 execute → 显示终态结果（succeeded / duplicate / failed / unknown）

每步都需要用户点击按钮触发，这是当前安全不变量的直接前端化。

### R3. 错误处理

- bridge 未连接时禁用所有操作按钮，显示安装提示
- inspect/match/prepare/execute 失败时显示错误消息
- unknown 状态显示「需要人工对账」提示
- match 返回 `skip` 或 `needs_review` 时显示决策原因，不继续后续步骤

### R4. 半自动 loop 模式（开关）

面板顶部一个 `Switch`「半自动模式」。开启后：

1. 步骤 1→2→3 **自动串行执行**（inspect 成功后自动 match，match 返回 communicate
   后自动 prepare）
2. 步骤 3 完成后 **停在 approval_required**，等待人工批准
3. 批准后 **停在 execute 前**，等待人工点击执行
4. 任何步骤失败 / 返回 skip / 返回 needs_review → 自动停止 loop，显示原因

**安全约束**：半自动 loop 只自动化 inspect→match→prepare（均为只读 / 无副作用操作），
execute 始终需要人工确认。**不违反** "no batch paths" 和 "execute needs approval"
两条安全不变量——前端 loop 本质是串行调用单职位端点，后端仍按单职位单请求处理。

**StrictMode 防重复**：使用 `useRef` + 复合 key（`application.id:step`）防止 React
StrictMode 开发模式下 effect 重复触发导致的重复 API 调用。

## Acceptance Criteria

- [x] `RecommendedJobPilotPanel` 组件已实现
- [x] 可显示 bridge 连接状态
- [x] 可显示 JD 预览
- [x] 可显示匹配决策和开场白
- [x] 可执行 inspect / match / prepare / approve / execute 流程
- [x] 错误状态有清晰提示
- [x] 半自动 loop 模式开关（inspect→match→prepare 自动，approve/execute 人工）
- [x] 步骤进度可视化（Steps 组件）
- [x] 挂载到 `ApplicationsPage`（仅 BOSS 平台应用显示）
- [ ] 前端 lint / type-check 通过

## Notes

- 设计文档：`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`
  第 45-51 行。
- 技术实现详见 `design.md`。
- 组件位于 `frontend/src/features/applications/RecommendedJobPilotPanel.tsx`。
- 挂载点：`ApplicationsPage.tsx`，作为 `GuidedSubmitPanel` 的兄弟组件，仅在
  `job.platform === "boss"` 时条件渲染。
- 半自动 loop 是阶段 A 的核心产出，为阶段 C（dry-run 门槛积累）提供工具。

