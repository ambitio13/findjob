# B2: 选择器漂移自动检测 — 实施清单

## 前置条件

- [x] B1 (`extra_selectors`) 已完成并测试通过
- [x] `design.md` 已编写

## 实施步骤

### Step 1: 在 `execute_communication()` 插入 Step 3.5 漂移探测

**文件**: `backend/app/platforms/boss/userscript_adapter.py`

在 Step 3（页面绑定验证 `current_hash != target_hash` 检查）的 `return` 块之后、
Step 4（`# Step 4: Click "立即沟通"` 注释）之前，插入：

```python
# Step 3.5: Selector drift detection — proactively probe the communicate
# entry buttons before clicking. If both IMMEDIATE_COMMUNICATE_BUTTON and
# CONTINUE_COMMUNICATE_BUTTON are invisible, the page markup has drifted.
# Read-only check (no click/fill); preserves safety invariants.
immediate_probe = await page.is_visible(
    _resolve(page, IMMEDIATE_COMMUNICATE_BUTTON)
)
continue_probe = await page.is_visible(
    _resolve(page, CONTINUE_COMMUNICATE_BUTTON)
)
if not immediate_probe and not continue_probe:
    _log.warning(
        "boss.userscript.communicate.selector_drift",
        immediate_visible=immediate_probe,
        continue_visible=continue_probe,
    )
    return CommunicationExecuteResult(
        outcome=CommunicationOutcome.failed,
        failure_code="selector_drift",
        message=(
            "Selector drift detected: both IMMEDIATE_COMMUNICATE_BUTTON "
            "and CONTINUE_COMMUNICATE_BUTTON are invisible. The BOSS page "
            "markup may have changed."
        ),
        diagnostic_reference=sanitize_diagnostic(
            "selector_drift_immediate_communicate"
        ),
        occurred_at=now,
    )
```

**注意**: `page.is_visible()` 已存在（`userscript_adapter.py:178`），内部发送
`check_visible` 指令。`_resolve()` 和 `IMMEDIATE_COMMUNICATE_BUTTON` /
`CONTINUE_COMMUNICATE_BUTTON` 已在文件顶部导入。

### Step 2: 新增测试 — 漂移检测触发

**文件**: `backend/app/tests/test_userscript_boss_adapter.py`

在文件末尾（B1 的两个 `extra_selectors` 测试之后）新增：

```python
async def test_communicate_selector_drift_returns_failed() -> None:
    """Both entry buttons invisible → selector_drift failure."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            # Both entry buttons invisible → drift.
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value): _visible_result(False),
            ("check_visible", CONTINUE_COMMUNICATE_BUTTON.value): _visible_result(False),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())

    assert result.outcome == CommunicationOutcome.failed
    assert result.failure_code == "selector_drift"
    assert result.diagnostic_reference == sanitize_diagnostic(
        "selector_drift_immediate_communicate"
    )

    # Safety: no click instructions sent when drift is detected.
    clicks = [
        ins for ins in ch.instructions_sent
        if ins.op in ("click_immediate_communicate", "send_opening_message")
    ]
    assert clicks == []
```

### Step 3: 新增测试 — 正常流程不受影响

```python
async def test_communicate_drift_check_passes_when_immediate_visible() -> None:
    """Immediate button visible → normal flow proceeds, no drift failure."""
    ch = FakeUserscriptChannel(
        result_map={
            ("read_url", None): _url_result(),
            # Immediate button visible → drift check passes.
            ("check_visible", IMMEDIATE_COMMUNICATE_BUTTON.value): _visible_result(True),
            ("click_immediate_communicate", IMMEDIATE_COMMUNICATE_BUTTON.value): _ok_result(),
            ("fill_opening_message", COMMUNICATION_MESSAGE_INPUT.value): _ok_result(),
            ("send_opening_message", COMMUNICATION_SEND_BUTTON.value): _ok_result(),
            ("read_communication_result", None): _marker_result(success_count=1),
        }
    )
    adapter = UserscriptBossAdapter(channel=ch)
    result = await adapter.execute_communication(_communicate_ctx())

    assert result.outcome == CommunicationOutcome.succeeded

    # Drift check sent exactly 1 check_visible for the immediate button.
    drift_checks = [
        ins for ins in ch.instructions_sent
        if ins.op == "check_visible"
        and ins.selector_value == IMMEDIATE_COMMUNICATE_BUTTON.value
    ]
    assert len(drift_checks) == 1
```

### Step 4: ruff + pytest 验证

```bash
cd backend
ruff check app/platforms/boss/userscript_adapter.py app/tests/test_userscript_boss_adapter.py
ruff format --check app/platforms/boss/userscript_adapter.py app/tests/test_userscript_boss_adapter.py
pytest app/tests/test_userscript_boss_adapter.py -v
```

## 验证清单

- [ ] `check_visible` 探测在页面绑定验证之后执行
- [ ] 两个入口按钮都不可见时返回 `failed` + `selector_drift`
- [ ] `diagnostic_reference` 标注具体漂移选择器
- [ ] 漂移时不发送任何 click 指令（安全）
- [ ] 正常流程（立即沟通可见）不受影响
- [ ] 现有 14 个 communicate 测试全部通过
- [ ] ruff check + format clean
- [ ] design.md 中的安全不变量分析确认
