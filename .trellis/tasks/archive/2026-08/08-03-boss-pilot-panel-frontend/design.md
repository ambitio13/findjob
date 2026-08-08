# RecommendedJobPilotPanel 技术设计

## 概述

`RecommendedJobPilotPanel` 是 BOSS 推荐职位引导沟通流程的前端 GUI 组件，编排五步
管线：inspect → match → prepare → approve → execute。每步调用单职位后端端点，
无批量路径。

**文件**: `frontend/src/features/applications/RecommendedJobPilotPanel.tsx`

## 组件结构

```
RecommendedJobPilotPanel (主组件)
├── BridgeStatusCard          — 油猴桥接状态（复用 useBridgeStatus hook）
├── Steps                     — 步骤进度可视化（Ant Design Steps）
├── InspectStepCard           — 步骤1：读取当前职位
│   └── JobPreview            — JD 预览（标题/公司/薪资/描述）
├── MatchStepCard             — 步骤2：匹配分析
├── PrepareApproveStepCard    — 步骤3-4：准备沟通 & 人工审批
└── ExecuteStepCard           — 步骤5：执行发送
```

## 状态管理

组件内 `useState` 管理，无全局状态。状态变量：

| 状态 | 类型 | 说明 |
|------|------|------|
| `step` | `PilotStep` | 当前步骤 (`inspect`/`match`/`prepare`/`approve`/`execute`/`done`) |
| `semiAuto` | `boolean` | 半自动模式开关 |
| `busy` | `boolean` | 是否有步骤正在执行（禁用按钮防重复） |
| `inspectResult` | `InspectJobOut \| null` | inspect 步骤结果 |
| `matchResult` | `MatchDecisionOut \| null` | match 步骤结果 |
| `prepareResult` | `CommunicatePrepareOut \| null` | prepare 步骤结果 |
| `executeResult` | `CommunicateExecuteOut \| null` | execute 步骤结果 |
| `openingMessage` | `string` | 可编辑的开场白（仅本地预览） |
| `error` | `string \| null` | 最近一次步骤错误 |

### 派生值

- `jobId` — 优先取 `inspectResult.job.id`，fallback 到 `application.job_id`
- `matchArtifactId` — 从 `matchResult.artifact_id` 取
- `action` — 从 `prepareResult.action` 或 `executeResult.action` 取
- `actionId` / `applicationId` — 从 action 派生

### 重置逻辑

- `application.id` 变化时（用户切换投递记录）→ 重置所有步骤状态
- `step` 变化时 → 清空 `autoFiredRef`（允许重新进入同一步骤时自动触发）
- 手动点击「重新开始」→ 重置所有状态

## API 调用

复用 `@/api/client` 中的函数：

| 步骤 | API 函数 | 后端端点 | 超时 |
|------|---------|---------|------|
| inspect | `inspectCurrentJob(resumeVersionId)` | `POST /boss/recommended-jobs/current/inspect` | 120s |
| match | `matchJob(jobId, resumeVersionId)` | `POST /boss/recommended-jobs/{job_id}/match` | 60s |
| prepare | `prepareCommunicate(jobId, resumeVersionId, matchArtifactId)` | `POST /boss/recommended-jobs/{job_id}/communicate/prepare` | 默认 |
| approve | `approveApplicationAction(applicationId, actionId)` | `POST /applications/{app_id}/actions/{action_id}/approve` | 默认 |
| execute | `executeCommunicate(jobId, actionId, applicationId)` | `POST /boss/recommended-jobs/{job_id}/communicate/{action_id}/execute` | 120s |

超时策略：inspect 和 execute 需要通过 userscript bridge 操作浏览器（受背景 tab 节流
影响），使用 120s 超时；match 是模型调用，使用 60s；其余用默认 15s。

## 半自动 loop 设计

### 驱动机制

一个 `useEffect` 监听 `[semiAuto, step, busy, application.id]`：

1. `semiAuto` 为 false → 不触发
2. `busy` 为 true → 不触发（上一步还在执行）
3. 当前步骤不是 inspect/match/prepare → 不触发（approve/execute 始终人工）
4. 已对当前步骤触发过 → 不触发（去重）

### StrictMode 防重复

使用 `useRef<string>` (`autoFiredRef`) 存储复合 key
`${application.id}:${step}`。React StrictMode 开发模式下 effect 会执行两次，
ref 去重确保同一步骤只自动触发一次。

`step` 变化时清空 ref，允许重新进入同一步骤（如手动重置后）时再次触发。

### 自动停止条件

以下情况自动关闭半自动模式（`setSemiAuto(false)`）：

- inspect 返回非 `ok` 状态（`jd_too_sparse` / `read_failed`）
- match 返回非 `communicate` 决策（`skip` / `needs_review`）
- 任何步骤抛出异常

### 安全保证

半自动 loop **只自动化只读/无副作用步骤**：

| 步骤 | 副作用 | 自动化？ |
|------|--------|---------|
| inspect | 只读（读取 JD） | ✅ |
| match | 只读（模型推理） | ✅ |
| prepare | 创建 action 记录（无平台操作） | ✅ |
| approve | 更新 action 状态（无平台操作） | ❌ 人工 |
| execute | 真实发送消息至 BOSS 平台 | ❌ 人工 |

后端 "no batch paths" 不变量不受影响——前端 loop 本质是串行调用单职位端点，
后端仍按单职位单请求处理。

## 挂载方式

在 `ApplicationsPage.tsx` 的 `ApplicationDetail` 组件中，作为 `GuidedSubmitPanel`
的兄弟组件，仅在 `job?.platform === "boss"` 时条件渲染：

```tsx
{job?.platform === "boss" ? (
  <RecommendedJobPilotPanel application={app} onAfterChange={load} />
) : null}
```

## 复用的基础设施

- `useBridgeStatus(enabled)` — 5s 轮询 `/userscript-bridge/status`，返回桥接状态
- `apiErrorMessage(err)` — 统一 API 错误消息提取
- `approveApplicationAction` — 已有审批函数，直接复用
- Ant Design 组件：Card, Steps, Button, Switch, Alert, Descriptions, Tag, Input, Popconfirm

## 已知限制

1. **开场白编辑**：UI 允许编辑开场白，但后端 prepare 阶段使用 match artifact 中
   存储的开场白，不使用前端编辑值。组件内有提示文字说明此限制。
2. **单职位流**：组件编排单个职位的完整流程，不处理推荐列表批量遍历（阶段 D 的
  范围）。
3. **无进度持久化**：组件状态在内存中，页面刷新后重置。步骤结果需从后端重新获取。
