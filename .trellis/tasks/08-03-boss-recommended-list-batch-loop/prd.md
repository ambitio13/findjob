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

- 从推荐列表中逐个取出职位
- 对每个职位执行：inspect → match → prepare → approve → execute
- 串行执行（受「单活跃 application」不变量约束）

### R2. 失败-first 硬停止

- 单职位失败（failed / unknown）不阻塞后续职位
- 但连续多次失败应触发硬停止（阈值在 `design.md` 中确定）
- 每个职位的结果独立记录

### R3. 进度跟踪

- 显示当前处理到第几个职位 / 总数
- 显示每个职位的结果（succeeded / duplicate / failed / unknown / skipped）
- 支持暂停/恢复

## Acceptance Criteria

- [ ] 可从推荐列表中批量处理职位
- [ ] 单职位失败不阻塞后续
- [ ] 连续失败触发硬停止
- [ ] 每个职位结果独立记录
- [ ] 进度可跟踪
- [ ] 全部后端测试通过
- [ ] ruff / 前端 lint clean

## Notes

- 需要编写 `design.md` 确定批量处理的 API 设计（新端点 vs 复用现有端点 + 轮询）。
- 建议在 P3-1（前端 PilotPanel 组件）完成后实施，前端需要支持进度显示。
- 安全不变量「单活跃 application」意味着批量处理本质上是串行的。
