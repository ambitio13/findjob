# B2: 选择器漂移自动检测 — 技术设计

## 1. 问题分析

### 1.1 现状

`execute_communication()` 流程（`userscript_adapter.py:818`）：

```
1. 验证 bridge 连接
2. set_active_application
3. 验证页面绑定 (URL hash 匹配)
4. 点击「立即沟通」← 如果选择器漂移，这里抛异常
5. 填充开场白 ← 如果选择器漂移，这里重试 5 次后失败
6. 验证页面 hash 未变
7. 点击发送
8. 读取 marker 并分类
```

当前问题：如果 `IMMEDIATE_COMMUNICATE_BUTTON` 选择器因 BOSS 页面改版而失效，
异常在第 4 步才触发，返回 `failed` + `failure_code="immediate_button_missing"`。
但这个 failure_code 无法区分"选择器漂移"和"该职位确实没有立即沟通按钮"两种情况。

同理，如果 `COMMUNICATION_MESSAGE_INPUT` 漂移，第 5 步返回 `failed` +
`failure_code="message_input_missing"`，同样无法区分漂移与真实缺失。

### 1.2 prepare 阶段无法检测

`prepare_communicate_action()`（`boss_communicate_service.py:123`）是**纯 DB 操作**，
不触碰浏览器。它只读取 match artifact、创建 `approval_required` 的 ApplicationAction。
没有 adapter 调用、没有 userscript 交互。

因此，PRD 中"在 prepare 阶段检查关键选择器"的设想需要修正：漂移检测必须放在
`execute_communication()` 中，因为只有 execute 才会真正操作浏览器。

### 1.3 与 submit 流程的 `classify_page()` 对比

submit 流程在 prepare 阶段调用 `classify_page()`，检查 `MESSAGE_INPUT` +
`FINAL_SUBMIT_BUTTON` 是否可见，缺失则返回 `SELECTOR_DRIFT`。这可行是因为
submit 的 prepare 阶段确实会操作浏览器（读取页面状态）。

communicate 流程没有这样的 prepare 阶段——它的 prepare 是纯 DB 操作。
所以我们需要在 execute 的早期阶段加入等价的漂移检测。

## 2. 方案

### 2.1 插入点

在 `execute_communication()` 第 3 步（页面绑定验证）之后、第 4 步（点击立即沟通）
之前，插入新的 **Step 3.5: 选择器漂移探测**。

```
1. 验证 bridge 连接
2. set_active_application
3. 验证页面绑定 (URL hash 匹配)
3.5 【新增】选择器漂移探测 ← check_visible 探测关键选择器
4. 点击「立即沟通」
5. 填充开场白
...
```

### 2.2 使用 `check_visible` 而非 `probe_elements`

| 维度 | `check_visible` | `probe_elements` |
|------|-----------------|------------------|
| 返回值 | `visible: bool` + `count: int` | `text: str` (JSON 元素详情) + `count: int` |
| 用途 | 可见性检查（布尔判定） | DOM 诊断（元素 tag/class/text 详情） |
| 轻重 | 轻 — 只返回布尔值 | 重 — 返回最多 20 个元素的 JSON 详情 |
| 适配漂移检测 | ✅ 完美匹配 | ❌ 过重，且 `visible` 字段不会被设置 |

**决策**: 使用 `check_visible`。漂移检测只需要知道"选择器是否匹配到可见元素"，
不需要元素详情。`probe_elements` 是诊断端点 `POST /userscript-bridge/probe` 用的，
不适合常规流程。

### 2.3 关键选择器清单

communicate 流程的关键选择器（在点击立即沟通之前可检测的）：

| 选择器常量 | 语义 | 备注 |
|-----------|------|------|
| `IMMEDIATE_COMMUNICATE_BUTTON` | 「立即沟通」按钮 | 主入口，必须可见 |
| `CONTINUE_COMMUNICATE_BUTTON` | 「继续沟通」按钮 | 备选入口（已有对话时显示） |

**注意**: `COMMUNICATION_MESSAGE_INPUT` 和 `COMMUNICATION_SEND_BUTTON` 在点击
「立即沟通」之前不可见（它们在聊天面板中，面板需要点击后才会展开）。因此这两者
不能在 Step 3.5 探测，只能在点击后再检查（现有代码已在 Step 4/5 中处理）。

**漂移检测逻辑**:
- 如果 `IMMEDIATE_COMMUNICATE_BUTTON` **和** `CONTINUE_COMMUNICATE_BUTTON` 都不可见
  → `selector_drift`（两个入口选择器都失效，说明页面改版）
- 如果 `IMMEDIATE_COMMUNICATE_BUTTON` 可见 → 正常继续（点击它）
- 如果只有 `CONTINUE_COMMUNICATE_BUTTON` 可见 → 正常继续（点击它，已有对话的场景）
- 如果两者都可见 → 正常继续（优先点击 `IMMEDIATE_COMMUNICATE_BUTTON`，现有逻辑已处理）

这与现有 Step 4 的异常处理逻辑一致：先尝试立即沟通，失败后检查聊天是否已展开，
再尝试继续沟通。区别在于：Step 3.5 是**主动探测**，Step 4 是**异常后补救**。

### 2.4 outcome 映射

漂移检测失败时返回：

```python
CommunicationExecuteResult(
    outcome=CommunicationOutcome.failed,
    failure_code="selector_drift",
    message="Selector drift detected: IMMEDIATE_COMMUNICATE_BUTTON and "
            "CONTINUE_COMMUNICATE_BUTTON are both invisible. The BOSS page "
            "markup may have changed.",
    diagnostic_reference=sanitize_diagnostic("selector_drift_immediate_communicate"),
    occurred_at=now,
)
```

使用 `CommunicationOutcome.failed`（而非 `unknown`），因为：
- `failed` 表示"操作失败，需要人工检查"——漂移正是这种情况
- `unknown` 保留给"操作已执行但结果不明"的场景（如已点击发送但读不到 marker）
- 漂移是操作前的确定性检查失败，属于 `failed` 范畴

`failure_code="selector_drift"` 区分于现有的 `"immediate_button_missing"`：
- `selector_drift` = 两个入口选择器都探测不到（页面改版，需要更新选择器）
- `immediate_button_missing` = 点击立即沟通失败且继续沟通也失败（可能只是该职位无沟通入口）

### 2.5 探测操作的只读性

`check_visible` 是纯只读操作：
- userscript handler 只调用 `querySelectorAll` + `isElementVisible`
- 不修改 DOM、不点击、不填充
- 不影响安全不变量（"prepare never clicks" / "execute needs approval" 均不受影响）

### 2.6 与现有 Step 4 异常处理的关系

现有 Step 4 已有类似逻辑：点击立即沟通失败后，检查 `COMMUNICATION_MESSAGE_INPUT`
是否可见（聊天面板已展开），再尝试 `CONTINUE_COMMUNICATE_BUTTON`。

Step 3.5 的区别：
- **时机**: Step 3.5 在点击前主动探测；Step 4 在点击失败后被动补救
- **检测对象**: Step 3.5 检测入口按钮；Step 4 检测聊天面板是否已展开
- **失败语义**: Step 3.5 失败 = `selector_drift`（页面改版）；Step 4 失败 =
  `immediate_button_missing`（特定职位无入口）

两者互补，不冲突。Step 3.5 通过后，Step 4 的异常处理保持不变。

## 3. 文件变更

### 3.1 `backend/app/platforms/boss/userscript_adapter.py`

在 `execute_communication()` 中，Step 3（页面绑定验证）之后、Step 4（点击立即沟通）
之前，插入 Step 3.5：

```python
# Step 3.5: Selector drift detection — proactively probe the communicate
# entry buttons before clicking. If both IMMEDIATE_COMMUNICATE_BUTTON and
# CONTINUE_COMMUNICATE_BUTTON are invisible, the page markup has drifted.
# This is a read-only check (no click/fill) and preserves safety invariants.
immediate_probe = await page.is_visible(_resolve(page, IMMEDIATE_COMMUNICATE_BUTTON))
continue_probe = await page.is_visible(_resolve(page, CONTINUE_COMMUNICATE_BUTTON))
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
            "Selector drift detected: both IMMEDIATE_COMMUNICATE_BUTTON and "
            "CONTINUE_COMMUNICATE_BUTTON are invisible. The BOSS page markup "
            "may have changed."
        ),
        diagnostic_reference=sanitize_diagnostic(
            "selector_drift_immediate_communicate"
        ),
        occurred_at=now,
    )
```

`page.is_visible()` 内部发送 `check_visible` 指令，已存在于 `UserscriptBossPage`
（`userscript_adapter.py:178`）。

### 3.2 测试: `backend/app/tests/test_userscript_boss_adapter.py`

新增 2 个测试：

1. **`test_communicate_selector_drift_returns_failed`**
   - result_map 中 `IMMEDIATE_COMMUNICATE_BUTTON` 和 `CONTINUE_COMMUNICATE_BUTTON`
     的 `check_visible` 都返回 `visible=False`
   - 断言: `outcome == CommunicationOutcome.failed`
   - 断言: `failure_code == "selector_drift"`
   - 断言: `diagnostic_reference == sanitize_diagnostic("selector_drift_immediate_communicate")`
   - 断言: 没有 `click_immediate_communicate` 指令被发送（安全：漂移时不点击）

2. **`test_communicate_drift_check_skipped_when_immediate_visible`**
   - result_map 中 `IMMEDIATE_COMMUNICATE_BUTTON` 的 `check_visible` 返回 `visible=True`
   - 后续步骤正常设置（click + fill + send + markers）
   - 断言: `outcome == CommunicationOutcome.succeeded`（正常流程不受影响）
   - 断言: `check_visible` 指令被发送了 1 次（探测立即沟通按钮）

### 3.3 无需变更的文件

- `userscript_channel.py` — `check_visible` op 和 `InstructionResult.visible` 已存在
- `userscript_bridge.py` (schema) — 无新字段
- `userscript_bridge.py` (API) — 无新端点
- `boss_communicate_service.py` — execute service 层不需要改动，adapter 层处理
- `selectors.py` — 使用现有常量
- `classifiers.py` — communicate 流程不调用 `classify_page()`，漂移检测在 adapter 内
- `docs/boss-userscript.user.js` — `check_visible` handler 已存在，无需改动

## 4. 安全不变量分析

| 不变量 | 影响 |
|--------|------|
| prepare never clicks final submit | ✅ 无影响 — 漂移检测在 execute 中，且只读 |
| execute needs approval | ✅ 无影响 — 漂移检测在 execute 内部，execute 本身已需审批 |
| at most 1 click_immediate + 1 send | ✅ 无影响 — 漂移检测不增加任何 click |
| no navigation | ✅ 无影响 — `check_visible` 是纯 DOM 查询 |
| in-memory only | ✅ 无影响 — 不持久化探测结果 |
| single active application | ✅ 无影响 — 在 `set_active_application` 之后执行 |

## 5. 已知限制

1. **只能检测入口按钮漂移**: `COMMUNICATION_MESSAGE_INPUT` 和 `COMMUNICATION_SEND_BUTTON`
   的漂移仍需在点击后才能发现（现有 Step 5/7 的异常处理覆盖）。
2. **误报可能**: 如果用户停留在非推荐列表页面（如已打开的聊天页），两个入口按钮
   都不可见，会误报为漂移。但 Step 3 的 URL hash 验证已经排除了大部分错页场景。
3. **`check_visible` 的 `isElementVisible` 判定**: 元素零宽高、`display:none`、
   `visibility:hidden`、`opacity:0` 均判定为不可见。如果 BOSS 用 `visibility:hidden`
   隐藏按钮但保留在 DOM 中，会触发漂移告警。这是保守行为，符合安全设计。
