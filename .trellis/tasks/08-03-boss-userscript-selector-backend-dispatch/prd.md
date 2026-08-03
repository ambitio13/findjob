# userscript 选择器改为后端下发

## Goal

消除油猴脚本中硬编码的选择器与 `backend/app/platforms/boss/selectors.py` 漂移的风险。
这是 B1 bug 的根因——油猴脚本和后端各自维护一份选择器，修改一侧时另一侧容易遗漏。

## Confirmed Facts

- B1 的根因就是油猴脚本 `read_communication_result` op 硬编码了 error 选择器
  （`.error-message, .toast-error, .dialog-error`），与 `selectors.py` 中的
  `PLATFORM_ERROR_MARKER`（`.error-tip, .error-content, .upload-error`）不一致。
- 当前油猴脚本中 `read_communication_result` op 硬编码了 3 组选择器：
  success marker、duplicate marker、error marker。
- 后端 `Instruction` 数据结构已支持 `selector_kind`、`selector_value`、`selector_name`
  字段，但 `read_communication_result` 指令不携带选择器。
- 后端 `_classify_communication_markers()` 已在后端做分类决策（B3 修复），userscript
  只需用收到的选择器查询 DOM 并返回计数。

## Requirements

### R1. 指令携带选择器

`read_communication_result` 指令从后端携带 3 组选择器（success / duplicate / error），
而非由 userscript 硬编码。具体方案：

- `Instruction` 新增字段或复用现有字段传递多组选择器（需在 `design.md` 中确定方案）
- 后端在 `execute_communication()` 中从 `selectors.py` 读取
  `COMMUNICATION_SUCCESS_MARKER`、`COMMUNICATION_DUPLICATE_MARKER`、
  `PLATFORM_ERROR_MARKER` 的值，放入指令
- userscript 从指令中读取选择器，用 `querySelectorAllWithTextFilter()` 查询 DOM

### R2. userscript 移除硬编码

- `boss-userscript.user.js` 中 `read_communication_result` op 不再硬编码任何选择器
- 选择器变更只需修改 `selectors.py`，userscript 自动生效

### R3. 向后兼容

- 旧版 userscript（不读取指令中选择器）应优雅降级或被检测到并提示更新

## Acceptance Criteria

- [ ] `read_communication_result` 指令携带 3 组选择器
- [ ] userscript 从指令中读取选择器，不再硬编码
- [ ] 修改 `selectors.py` 中的选择器后，userscript 自动使用新选择器（无需改 JS）
- [ ] 全部后端测试通过
- [ ] ruff clean
- [ ] `docs/boss-communicate-testing-plan.md` 中 P2-2 标记为已完成

## Notes

- 需要编写 `design.md` 确定指令携带多组选择器的具体方案（新字段 vs 复用现有字段）。
- 参考 `docs/boss-communicate-testing-plan.md` 第 4 节 P2-2。
