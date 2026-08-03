# 10 次 dry-run 门槛验证

## Goal

积累至少 10 次真实 dry-run 无事故记录，满足设计文档 Rollout Plan step 6 的门槛要求。
在 10 次 dry-run 无 wrong-tab / duplicate 误发 / unknown 误停事故后，才可启用 auto-execute。

## Confirmed Facts

- 设计文档 Rollout Plan step 6 要求"至少 10 次真实 dry-run 无事故"后才可启用
  auto-execute。
- 当前 0 次 dry-run 记录。
- 此任务依赖 P0-2（真实页面验证）通过后才能开始积累。

## Requirements

### R1. Dry-run 记录

每次 dry-run 记录以下信息到 `check.jsonl` 或开发日志：

- 日期 + 操作者
- 目标职位 URL（脱敏为 hash）
- `read_communication_result` 实际返回值
- `external_result_status`
- 是否有异常（选择器漂移、超时、意外弹窗等）
- 人工对账结论（如果 `unknown`，登录 BOSS 确认消息是否已发送）

### R2. 事故定义

以下情况计为「事故」：
- **wrong-tab**：指令在非目标 tab 上执行
- **duplicate 误发**：对话已存在但被分类为 `succeeded`（向已沟通的 HR 重复发送消息）
- **unexpected unknown**：页面状态明确（成功或重复）但被分类为 `unknown`

### R3. 门槛规则

- 连续 10 次 dry-run 无事故 → 通过门槛
- 出现任何事故 → 计数归零，修复后重新开始
- 10 次中至少包含 2 次 `duplicate_detected` 场景（验证重复检测正确）

## Acceptance Criteria

- [ ] 10 次 dry-run 记录已归档
- [ ] 0 事故（0 wrong-tab / 0 duplicate 误发 / 0 unexpected unknown）
- [ ] 至少 2 次 `duplicate_detected` 场景
- [ ] 验证记录包含完整信息（日期、操作者、脱敏 URL、返回值、异常情况、对账结论）

## Notes

- 依赖 P0-2（真实页面验证）通过后开始。
- 这是手动验证任务，无代码产出。
- 记录格式参考 `docs/boss-communicate-testing-plan.md` 第 3 节「验证记录要求」。
