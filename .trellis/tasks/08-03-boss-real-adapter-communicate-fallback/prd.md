# RealBossAdapter.execute_communication() CDP fallback

## Goal

为 communicate 流程实现 CDP（Playwright）fallback。当前 communicate 只有 fake（测试）和
userscript（真实）路径，无 CDP fallback。如果 userscript 路径不可用且 CDP 可用，需要
fallback。

## Confirmed Facts

- 当前 `RealBossAdapter`（Playwright/CDP 路径）没有实现 `execute_communication()`。
- `UserscriptBossAdapter` 是 communicate 的唯一真实执行路径。
- BOSS 的反自动化检测使 CDP 在某些场景下不可用，但在未触发检测时 CDP 可用。
- `RealBossAdapter` 已实现 `prepare_submission()` 和 `submit_prepared()`，communicate 是
  缺失的方法。

## Requirements

### R1. execute_communication() 实现

在 `RealBossAdapter` 中实现 `execute_communication()`：
- 使用 Playwright `BossPage` 执行 communicate 流程
- 流程与 `UserscriptBossAdapter.execute_communication()` 一致：
  1. 验证页面绑定
  2. 点击"立即沟通"
  3. 填充开场白
  4. 验证页面 hash 不变
  5. 点击发送
  6. 用 `classify_communication_result()` 分类结果
- 保持安全不变量：至多 1 click + 1 send

### R2. Fallback 逻辑

- 当 `boss_userscript_bridge_enabled` 为 false 或 userscript 未连接时，自动使用
  `RealBossAdapter`
- 当 userscript 连接时，优先使用 `UserscriptBossAdapter`

### R3. 分类一致性

- CDP 路径使用 `classify_communication_result()`（Python classifier）
- userscript 路径使用 `_classify_communication_markers()`（marker counts → Python classifier）
- 两者优先级一致：duplicate → success → error → unknown

## Acceptance Criteria

- [ ] `RealBossAdapter.execute_communication()` 已实现
- [ ] CDP 路径 communicate 可执行
- [ ] fallback 逻辑正确（userscript 优先，CDP fallback）
- [ ] 分类结果与 userscript 路径一致
- [ ] 安全不变量保持（至多 1 click + 1 send）
- [ ] 新增测试覆盖 CDP 路径 communicate
- [ ] 全部后端测试通过
- [ ] ruff clean

## Notes

- 需要编写 `design.md` 确定 fallback 的触发逻辑和适配器选择策略。
- `classify_communication_result()` 在 B2/B3 修复后已是正确的、非死代码的 Python classifier。
