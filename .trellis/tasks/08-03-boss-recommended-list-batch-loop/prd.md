# 推荐列表大循环

## Goal

从单职位闭环扩展到推荐列表批量处理。当前只做单个职位的 inspect → match → prepare →
approve → execute 闭环，不做推荐列表批量处理。

## Confirmed Facts

- 设计文档 Rollout Plan step 7 提到了推荐列表大循环，但未实现。
- 当前后端 API 支持单职位操作，无批量端点。
- `RealBossAdapter` / `UserscriptBossAdapter` 的 `execute_communication()` 每次处理一个
  职位。
- 安全不变量「单活跃 application」限制了并发，批量处理需要串行。

## Requirements

### R1. 批量遍历

- 后端新增 `POST /boss/recommended-jobs/batch-loop` 端点
- 串行遍历推荐列表（受「单活跃 application」不变量约束）
- 对每个职位执行：inspect → match → prepare → auto-approve（门槛通过后）→ execute
- 返回 batch run id，前端轮询进度

### R2. auto-execute 安全门控

- `auto-execute` 开关**默认关闭**
- 只有阶段 C（dry-run 门槛）通过后才能开启——10 次连续无事故 + 至少 2 次 duplicate
- 开关状态持久化在后端配置中，不由前端控制
- 开关关闭时：批量循环在 prepare 后停止，等待人工逐个审批（即半自动 loop 的批量版）
- 开关开启时：批量循环自动 approve + execute，但连续 N 次失败触发硬停止

### R3. 失败硬停止

- 连续 N 次失败（failed / unknown）触发硬停止（N 在 `design.md` 中确定，建议 3）
- 每个职位的结果独立记录
- 硬停止后前端显示失败详情，需人工确认后才能恢复

### R4. 前端扩展

- PilotPanel 扩展「批量模式」标签页
- 显示推荐列表 + 每个职位处理状态
- 进度条（当前 N/总数）
- 暂停/恢复按钮
- 每个职位结果独立显示

## Acceptance Criteria

- [ ] 可从推荐列表中批量处理职位
- [ ] auto-execute 开关默认关闭，门槛通过后才能开启
- [ ] 连续失败触发硬停止
- [ ] 每个职位结果独立记录
- [ ] 进度可跟踪（前端批量模式面板）
- [ ] 支持暂停/恢复
- [ ] 全部后端测试通过
- [ ] ruff / 前端 lint clean

## Notes

- 这是阶段 D 的任务，**硬前置**：阶段 C（10 次 dry-run 无事故）必须先通过。
- 渐进式方案：先实现半自动批量（prepare 后停），再实现全自动批量（门槛通过后）。
- 安全不变量「单活跃 application」意味着批量处理本质上是串行的。
- 建议编写 `design.md` 确定批量端点 API 设计、连续失败阈值、auto-execute 开关持久化方案。

