# Design: userscript 选择器改为后端下发

## 问题回顾

B1 bug 根因：userscript `read_communication_result` op 硬编码了 3 组 CSS 选择器
（success / duplicate / error），与后端 `selectors.py` 常量各自维护。修改一侧时
另一侧容易遗漏，导致分类漂移。

userscript `send_opening_message` 的 Enter-key 路径也硬编码了 textarea 选择器
（user.js:605-606），与后端 `COMMUNICATION_MESSAGE_INPUT` 漂移。

## 方案：`extra_selectors` 字段

### 数据结构变更

`Instruction` dataclass（`userscript_channel.py:82`）新增字段：

```python
extra_selectors: dict[str, str] | None = None
# key = marker name ("success" / "duplicate" / "error" / "message_input")
# value = CSS selector string（从 selectors.py 常量读取 .value）
```

设计理由：
- `dict[str, str]` 比复用 `selector_value`（单个 str）更灵活，一条指令可携带
  多组不同用途的选择器。
- key 用语义名称（而非 CSS 类名），后端改选择器值时 key 不变，userscript 代码
  无需改动。
- value 直接传 CSS selector string（`Selector.value`），userscript 用
  `querySelectorAllWithTextFilter` 解析（支持 `:has-text()` 伪选择器）。

### 涉及修改的文件

| 文件 | 变更 |
| --- | --- |
| `backend/app/platforms/boss/userscript_channel.py` | `Instruction` 新增 `extra_selectors` 字段；`make_instruction()` 新增参数；`put_instruction` auto-bind page_id 时透传该字段 |
| `backend/app/schemas/userscript_bridge.py` | `InstructionOut` 新增 `extra_selectors: dict[str, str] \| None` 字段 |
| `backend/app/api/v1/userscript_bridge.py` | `get_next_instruction` 构造 `InstructionOut` 时透传 `extra_selectors` |
| `backend/app/platforms/boss/userscript_adapter.py` | `read_communication_result()` 的 `make_instruction` 调用携带 3 组选择器；`send_opening_message()` 的 Enter-key 路径通过 `extra_selectors["message_input"]` 携带 textarea 选择器 |
| `docs/boss-userscript.user.js` | `read_communication_result` handler 从 `ins.extra_selectors` 读取；`send_opening_message` Enter-key 路径从 `ins.extra_selectors["message_input"]` 读取；移除硬编码 |
| `backend/app/tests/test_userscript_boss_adapter.py` | 验证 `read_communication_result` 和 `send_opening_message` 指令携带 `extra_selectors` |

### 后端 adapter 变更细节

#### `read_communication_result()`（userscript_adapter.py:306）

当前：
```python
result = await self._send(
    make_instruction("read_communication_result")
)
```

改为：
```python
from app.platforms.boss.selectors import (
    COMMUNICATION_SUCCESS_MARKER,
    COMMUNICATION_DUPLICATE_MARKER,
    PLATFORM_ERROR_MARKER,
)

result = await self._send(
    make_instruction(
        "read_communication_result",
        extra_selectors={
            "success": COMMUNICATION_SUCCESS_MARKER.value,
            "duplicate": COMMUNICATION_DUPLICATE_MARKER.value,
            "error": PLATFORM_ERROR_MARKER.value,
        },
    )
)
```

#### `send_opening_message()`（userscript_adapter.py:292）

当前只传 `selector_kind/value/name`（send button selector）。Enter-key 路径需要
textarea 选择器，目前 userscript 硬编码。

改为额外传 `extra_selectors["message_input"]`：
```python
from app.platforms.boss.selectors import COMMUNICATION_MESSAGE_INPUT

result = await self._send(
    make_instruction(
        "send_opening_message",
        selector_kind=locator.kind,
        selector_value=locator.value,
        selector_name=locator.name,
        extra_selectors={
            "message_input": COMMUNICATION_MESSAGE_INPUT.value,
        },
    )
)
```

### `put_instruction` auto-bind 透传

`put_instruction`（userscript_channel.py:282）在 auto-bind page_id 时重建
`Instruction`。因为 `Instruction` 是 `frozen=True` dataclass，需要重建。必须
在新构造中透传 `extra_selectors`，否则 auto-bind 后该字段丢失。

```python
instruction = Instruction(
    instruction_id=instruction.instruction_id,
    op=instruction.op,
    selector_kind=instruction.selector_kind,
    selector_value=instruction.selector_value,
    selector_name=instruction.selector_name,
    fill_value=instruction.fill_value,
    page_id=self._active_page.page_id,
    expected_url_hash=instruction.expected_url_hash
    or self._active_page.page_url_hash,
    max_text_chars=instruction.max_text_chars,
    selector_profile=instruction.selector_profile,
    extra_selectors=instruction.extra_selectors,  # NEW
)
```

### userscript 变更细节

#### `read_communication_result` handler（user.js:645-676）

当前硬编码 3 组选择器字符串。改为从 `ins.extra_selectors` 读取，fallback 到
硬编码（向后兼容旧后端）：

```javascript
if (ins.op === "read_communication_result") {
  const selectors = ins.extra_selectors || {};
  const successSel = selectors.success ||
    ".chat-message:has-text('已发送'), ...";
  const duplicateSel = selectors.duplicate ||
    ".btn-start:has-text('继续沟通'), ...";
  const errorSel = selectors.error ||
    ".error-tip, .error-content, .upload-error";
  const successEls = querySelectorAllWithTextFilter(successSel);
  const duplicateEls = querySelectorAllWithTextFilter(duplicateSel);
  const errorEls = querySelectorAllWithTextFilter(errorSel);
  result.marker_counts = {
    success_count: successEls.length,
    duplicate_count: duplicateEls.length,
    error_count: errorEls.length,
  };
  return result;
}
```

#### `send_opening_message` Enter-key 路径（user.js:605-606）

当前硬编码 textarea 选择器。改为从 `ins.extra_selectors["message_input"]`
读取，fallback 到硬编码：

```javascript
if (ins.op === "send_opening_message") {
  const msgInputSel = (ins.extra_selectors && ins.extra_selectors.message_input) ||
    ".edit-area textarea, .chat-message textarea, ...";
  const textarea = document.querySelector(msgInputSel);
  // ... rest of Enter-key logic unchanged
}
```

### 向后兼容策略

**双层 fallback**：

1. **新后端 + 旧 userscript**：旧 userscript 不读 `extra_selectors`，仍用硬编码。
   功能正常，但选择器仍可能漂移（旧 userscript 不享受修复）。可接受——用户更新
   userscript 后即修复。
2. **旧后端 + 新 userscript**：新 userscript 读 `ins.extra_selectors`，得到
   `undefined`，fallback 到硬编码。功能正常。

**不实现 `userscript_version` 心跳字段检测**（PRD R3 原计划）。原因：
- fallback 机制已保证功能不中断。
- 版本检测增加复杂度但收益有限——用户仍需手动更新 userscript。
- 前端已有 bridge 状态展示，userscript 不更新时选择器漂移风险仍在但不影响功能。
- 如果未来需要，可作为独立任务追加。

### 安全不变量影响

- `extra_selectors` 只携带 CSS selector string，不含凭证/cookie/token。
- 符合 "Instructions are operation-only" 和 "Instructions are backend-constructed"
  两条不变量——选择器本就是后端下发的，现在扩展到 marker 选择器。
- 不影响 "Results are sanitized" — userscript 仍只返回 `marker_counts`（int）。
- 不影响 "No navigation instructions" — 只读取 DOM，不导航。
- 不影响 "The submit click is gated" — 不涉及 click 指令。

### 已知技术债（本期不处理）

`extractBossRecommendedJobV1`（user.js:175-265）有 10 个 JD 提取选择器完全在
userscript 侧，后端无对应常量。工作量大，本期标注为后续工作。

## 测试计划

### 单元测试（`test_userscript_boss_adapter.py`）

1. **新增测试**：`read_communication_result` 指令携带 `extra_selectors`，包含
   3 个 key（success/duplicate/error），值对应 `selectors.py` 常量的 `.value`。
2. **新增测试**：`send_opening_message` 指令携带 `extra_selectors["message_input"]`。
3. **现有测试不受影响**：mock channel 的 `put_instruction` 透传 `extra_selectors`，
   result_map 的 key 仍是 `(op, selector_value)`，不依赖 `extra_selectors`。

### 手动验证

后端 Docker 重建后：
1. 在 BOSS 页面执行 inspect → match → prepare → execute 流程
2. 检查 backend log 确认 `read_communication_result` 指令携带 `extra_selectors`
3. 修改 `selectors.py` 中某个 marker 常量，重建后端，验证 userscript 自动使用
   新选择器（无需改 JS）
