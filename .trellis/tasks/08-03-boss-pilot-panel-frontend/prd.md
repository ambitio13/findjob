# 前端 RecommendedJobPilotPanel 组件

## Goal

实现设计文档（`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`
第 45-51 行）中设计但未实现的 `RecommendedJobPilotPanel` 前端组件。该组件是用户操作
BOSS 自动沟通流程的主要交互界面。

## Confirmed Facts

- 设计文档第 45-51 行定义了组件布局和功能。
- 后端 API 已全部就绪：inspect、prepare、approve、execute 端点。
- 前端已有 bridge status 显示的基础设施。
- 当前用户需要通过 `curl` 命令操作整个流程，无 GUI。

## Requirements

### R1. 组件功能

`RecommendedJobPilotPanel` 组件包含以下区域：

1. **Bridge 状态** — 显示油猴脚本连接状态（已连接/未连接）、当前 `page_id`、心跳时间
2. **JD 预览** — 显示 `read_jd` 返回的职位信息（标题、公司、薪资、描述等）
3. **匹配决策** — 显示 `boss_match_decision` artifact 的决策结果（`communicate` / `skip`）
   和决策依据
4. **开场白预览** — 显示 `opening_message` 内容，支持人工审阅和编辑
5. **操作控制** — prepare / approve / execute 按钮，显示当前 action 状态

### R2. 交互流程

1. 用户导航到 BOSS 职位详情页 → 组件自动调用 inspect → 显示 JD
2. 匹配决策生成后 → 显示决策结果和开场白
3. 用户点击「准备」→ 调用 prepare → 显示 `approval_required` 状态
4. 用户审阅后点击「批准」→ 调用 approve → 显示 `approved` 状态
5. 用户点击「执行」→ 调用 execute → 显示终态结果（succeeded / duplicate / failed / unknown）

### R3. 错误处理

- bridge 未连接时禁用所有操作按钮
- prepare/execute 失败时显示错误消息
- unknown 状态显示「需要人工对账」提示

## Acceptance Criteria

- [ ] `RecommendedJobPilotPanel` 组件已实现
- [ ] 可显示 bridge 连接状态
- [ ] 可显示 JD 预览
- [ ] 可显示匹配决策和开场白
- [ ] 可执行 prepare / approve / execute 流程
- [ ] 错误状态有清晰提示
- [ ] 前端 lint / type-check 通过

## Notes

- 设计文档：`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`
  第 45-51 行。
- 需要编写 `design.md` 确定组件的技术实现（React 组件结构、状态管理、API 调用方式）。
- 建议在 P0-2（真实页面验证）通过后实施，确保后端流程在真实环境下可用。
