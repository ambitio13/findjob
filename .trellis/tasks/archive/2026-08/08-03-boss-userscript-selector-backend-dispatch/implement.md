# Implementation Plan: userscript 选择器改为后端下发

## 执行顺序

### Step 1: 后端 channel 层 — `Instruction` 新增 `extra_selectors`

**文件**: `backend/app/platforms/boss/userscript_channel.py`

- [ ] `Instruction` dataclass 新增 `extra_selectors: dict[str, str] | None = None`
- [ ] `make_instruction()` 新增 `extra_selectors` 参数并透传
- [ ] `put_instruction()` auto-bind page_id 重建 `Instruction` 时透传 `extra_selectors`
- [ ] 更新 `Instruction` docstring 说明新字段用途

### Step 2: 后端 schema 层 — `InstructionOut` 新增字段

**文件**: `backend/app/schemas/userscript_bridge.py`

- [ ] `InstructionOut` 新增 `extra_selectors: dict[str, str] | None = None` 字段
  - 添加 description: "CSS selectors for marker groups (success/duplicate/error/message_input), keyed by semantic name."

### Step 3: 后端 API 层 — 透传字段

**文件**: `backend/app/api/v1/userscript_bridge.py`

- [ ] `get_next_instruction` 构造 `InstructionOut` 时添加
  `extra_selectors=instruction.extra_selectors`

### Step 4: 后端 adapter 层 — 携带选择器

**文件**: `backend/app/platforms/boss/userscript_adapter.py`

- [ ] 导入 `COMMUNICATION_SUCCESS_MARKER`, `COMMUNICATION_DUPLICATE_MARKER`,
  `PLATFORM_ERROR_MARKER`, `COMMUNICATION_MESSAGE_INPUT` from selectors
- [ ] `read_communication_result()` 的 `make_instruction` 调用添加 `extra_selectors`
  携带 3 组 marker 选择器
- [ ] `send_opening_message()` 的 `make_instruction` 调用添加 `extra_selectors`
  携带 `message_input` 选择器
- [ ] 更新 `read_communication_result` docstring 说明选择器来源

### Step 5: userscript — 移除硬编码

**文件**: `docs/boss-userscript.user.js`

- [ ] `read_communication_result` handler 改为从 `ins.extra_selectors` 读取 3 组
  选择器，fallback 到硬编码
- [ ] `send_opening_message` Enter-key 路径改为从 `ins.extra_selectors.message_input`
  读取，fallback 到硬编码
- [ ] 更新 `@version` 到 0.4.0
- [ ] 更新相关注释说明选择器来源

### Step 6: 单元测试

**文件**: `backend/app/tests/test_userscript_boss_adapter.py`

- [ ] 新增测试：`read_communication_result` 指令携带 `extra_selectors`（3 个 key）
- [ ] 新增测试：`send_opening_message` 指令携带 `extra_selectors["message_input"]`
- [ ] 现有测试全部通过

### Step 7: 后端质量检查

- [ ] `ruff check` 通过
- [ ] `pytest backend/app/tests/test_userscript_boss_adapter.py` 通过
- [ ] `pytest backend/app/tests/test_userscript_bridge.py` 通过（如存在）

### Step 8: Docker 镜像重建 + 手动验证

- [ ] `docker compose build backend`
- [ ] `docker compose up -d backend`
- [ ] 验证 `/next-instruction` 响应包含 `extra_selectors` 字段
- [ ] 更新 `docs/boss-communicate-testing-plan.md` P2-2 标记为已完成

## 验证清单

- [ ] `Instruction.extra_selectors` 字段存在且有默认值 `None`
- [ ] `make_instruction()` 透传 `extra_selectors`
- [ ] `put_instruction()` auto-bind 透传 `extra_selectors`
- [ ] `InstructionOut` schema 新增字段
- [ ] `get_next_instruction` 透传字段
- [ ] `read_communication_result` 携带 3 组选择器
- [ ] `send_opening_message` 携带 message_input 选择器
- [ ] userscript 从 `ins.extra_selectors` 读取，有 fallback
- [ ] userscript `@version` 更新
- [ ] 单元测试覆盖新字段
- [ ] ruff clean
- [ ] 全部测试通过
- [ ] Docker 镜像重建成功
