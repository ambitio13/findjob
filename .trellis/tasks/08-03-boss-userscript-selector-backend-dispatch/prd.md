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

### R1. 指令携带选择器（`extra_selectors` 方案）

`read_communication_result` 指令从后端携带 3 组选择器（success / duplicate / error），
而非由 userscript 硬编码。具体方案：

- `Instruction` dataclass 新增 `extra_selectors: dict[str, str] | None = None` 字段
  - key = marker name（`"success"` / `"duplicate"` / `"error"` / `"message_input"`）
  - value = CSS selector string（从 `selectors.py` 常量读取）
- `make_instruction()` 新增 `extra_selectors` 参数
- `InstructionOut` schema 新增对应字段
- 后端 `userscript_adapter.py` 中 `read_communication_result` 的 `make_instruction()`
  调用改为携带 3 组选择器（从 `selectors.py` 读取 `COMMUNICATION_SUCCESS_MARKER`、
  `COMMUNICATION_DUPLICATE_MARKER`、`PLATFORM_ERROR_MARKER`）
- `send_opening_message` 的 Enter-key 路径改用 `extra_selectors["message_input"]` 或
  复用 `selector_value` 字段传 `COMMUNICATION_MESSAGE_INPUT`
- userscript `read_communication_result` handler 改为从 `ins.extra_selectors` 读取，
  移除硬编码
- userscript `send_opening_message` Enter-key 路径改为从 `ins.selector_value` 或
  `ins.extra_selectors` 读取

### R2. userscript 移除硬编码

- `boss-userscript.user.js` 中 `read_communication_result` op 不再硬编码任何选择器
- `send_opening_message` 的 textarea 选择器也改为后端下发
- 选择器变更只需修改 `selectors.py`，userscript 自动生效

### R3. 向后兼容

- 旧版 userscript（不读取 `extra_selectors`）会 fallback 到硬编码——通过
  `userscript_version` 心跳字段检测，不匹配时前端提示更新

### R4. JD 提取选择器（已知技术债，本期不处理）

`extractBossRecommendedJobV1`（user.js:175-265）有 10 个 JD 提取选择器完全在 userscript
侧，后端无对应常量。这属于 P2-2 范围但工作量大，**本期暂不处理**，标注为后续工作。

## Acceptance Criteria

- [ ] `Instruction` 新增 `extra_selectors` 字段
- [ ] `read_communication_result` 指令携带 3 组选择器
- [ ] userscript 从指令中读取选择器，不再硬编码
- [ ] 修改 `selectors.py` 中的选择器后，userscript 自动使用新选择器（无需改 JS）
- [ ] `userscript_version` 心跳检测 + 前端更新提示
- [ ] 全部后端测试通过
- [ ] ruff clean
- [ ] `docs/boss-communicate-testing-plan.md` 中 P2-2 标记为已完成

## Notes

- 这是阶段 B 的核心任务，消除 B1 类 bug 的根因（选择器漂移）。
- `extra_selectors` 字段方案比复用现有 `selector_value` 更灵活，支持一组指令携带
  多组不同用途的选择器。
- 参考 `docs/boss-communicate-testing-plan.md` 第 4 节 P2-2。

