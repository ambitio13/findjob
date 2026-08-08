# BOSS 自动沟通（Immediate Communicate）测试与后续规划

> **文档定位**：本文档是 BOSS 自动沟通功能的综合测试 + 验证 + 后续路线图规划文档。
> 它不是 runbook（runbook 见 `docs/manual-boss-pilot.md` §8），而是在 runbook
> 之上对测试覆盖、已知问题、验证步骤和后续工作的系统性梳理。
>
> **创建日期**：2026-08-03
> **当前状态**：P1（`:has-text()` 伪选择器）和 P2（page_id 绑定）已修复并提交
> （commit `5496980`）。B1（油猴 error 选择器不一致）、B2（分类优先级反转）、
> B3（`classify_communication_result` 死代码）均已修复。758 测试通过，ruff 全部
> clean。真实 BOSS 页面验证尚未执行。

---

## 目录

1. [已知代码问题清单](#1-已知代码问题清单)
2. [测试覆盖现状分析](#2-测试覆盖现状分析)
3. [真实 BOSS 页面验证步骤](#3-真实-boss-页面验证步骤)
4. [后续路线图](#4-后续路线图)
5. [验收标准汇总](#5-验收标准汇总)

---

## 1. 已知代码问题清单

在 P1/P2 修复后的代码审查中，发现以下 3 个问题。它们不影响当前测试通过（因为
测试用 `FakeUserscriptChannel` 注入预定义结果，不经过真实 DOM），但会在真实
BOSS 页面上产生错误行为。

### B1: 油猴脚本 error 选择器与 `selectors.py` 不一致 ✅ 已修复

| 属性 | 值 |
|------|-----|
| **严重性** | P1 — 验收前必须修复 |
| **影响** | `platform_failure` 分类路径在真实页面上永远不会触发 |
| **状态** | 已修复（2026-08-03）|

**根因**：

油猴脚本 `docs/boss-userscript.user.js` 第 604-605 行的 `read_communication_result`
op 硬编码了 error 选择器：

```javascript
const errorEls = querySelectorAllWithTextFilter(
  ".error-message, .toast-error, .dialog-error",
);
```

但 `backend/app/platforms/boss/selectors.py` 第 124-128 行的 `PLATFORM_ERROR_MARKER`
定义的是：

```python
PLATFORM_ERROR_MARKER = Selector(
    kind=LocatorKind.CSS,
    value=".error-tip, .error-content, .upload-error",
    name=None,
)
```

两者完全不同——`.error-message` / `.toast-error` / `.dialog-error` 与
`.error-tip` / `.error-content` / `.upload-error` 没有任何交集。当真实 BOSS 页面
出现平台错误（如上传失败、限流提示）时，userscript 的 `errorEls` 会返回空数组，
`platform_failure` 路径永远不会走通，错误状态被降级为 `unknown`（硬停止）。

**修复方案**：

将 `docs/boss-userscript.user.js` 第 604-605 行改为与 `PLATFORM_ERROR_MARKER` 一致：

```javascript
const errorEls = querySelectorAllWithTextFilter(
  ".error-tip, .error-content, .upload-error",
);
```

同时补充注释说明：此选择器必须与 `selectors.py` 中 `PLATFORM_ERROR_MARKER` 保持
同步。后续应考虑将选择器由后端下发（见路线图 P2-3）。

---

### B2: 分类优先级反转 ✅ 已修复

| 属性 | 值 |
|------|-----|
| **严重性** | P2 — 生产前应该修复 |
| **影响** | 当页面同时显示成功标记和重复标记时，userscript 和 Python classifier 给出不同结果 |
| **状态** | 已修复（2026-08-03）|

**根因**：

油猴脚本 `read_communication_result` op（第 607-617 行）的分类顺序是
**duplicate → error → success**：

```javascript
if (duplicateEls.length > 0) {
  result.text = "duplicate_detected";
} else if (errorEls.length > 0) {
  result.text = "platform_failure";
} else if (successEls.length > 0) {
  result.text = "succeeded";
} else {
  result.text = "unknown";
}
```

但 Python `classify_communication_result()`（`classifiers.py` 第 185-196 行）的
分类顺序是 **success → duplicate → error**：

```python
if await _count(page, COMMUNICATION_SUCCESS_MARKER) > 0:
    return PageClassification(outcome="succeeded", ...)

if await _count(page, COMMUNICATION_DUPLICATE_MARKER) > 0:
    return PageClassification(outcome=DUPLICATE_DETECTED, ...)

if await _count(page, PLATFORM_ERROR_MARKER) > 0:
    return PageClassification(outcome="platform_failure", ...)
```

注意 `COMMUNICATION_SUCCESS_MARKER`（第 178 行）本身包含
`.btn-start:has-text('继续沟通')`，而 `COMMUNICATION_DUPLICATE_MARKER`（第 188 行）
也是 `.btn-start:has-text('继续沟通'), .chat-operate:has-text('继续沟通')`。这意味着
当页面显示"继续沟通"按钮时，两个 marker 会**同时匹配**——Python classifier 会返回
`succeeded`（success 先匹配），userscript 会返回 `duplicate_detected`（duplicate 先
匹配）。实际语义上，"继续沟通"按钮表示对话已存在（duplicate），所以 userscript 的
顺序在语义上是正确的，而 Python classifier 有 bug。

**修复方案（已实施方案 A）**：

修正 Python `classify_communication_result` 的顺序为 **duplicate → success → error**
（与 userscript 一致），同时在 `COMMUNICATION_SUCCESS_MARKER` 的注释中明确说明
"继续沟通"是 duplicate marker 而非 success marker。新增测试
`test_communicate_duplicate_priority_over_success` 作为回归防护。

---

### B3: `classify_communication_result` 是死代码 ✅ 已修复

| 属性 | 值 |
|------|-----|
| **严重性** | P3 — 健壮性问题 |
| **影响** | Python 分类器永远不会被调用，分类逻辑分散在 userscript 和 adapter 两处 |
| **状态** | 已修复（2026-08-03，实施方案 A）|

**根因**：

`backend/app/platforms/boss/userscript_adapter.py` 第 67 行的 import 只引入了
`classify_submit_result`，不引入 `classify_communication_result`。communicate 流程
的分类完全由 userscript 的 `read_communication_result` op 完成（在 JS 里检查标记
并返回字符串），adapter 的 `_communication_result_from_text()` 只是做字符串→枚举的
映射。

这违反了设计文档的核心原则——"Backend owns platform failure classification"（见
`design.md` 第 119-120 行）。当前架构中，分类决策（哪些标记算成功/重复/错误）实际
由 userscript 做，后端只做字符串映射。

**修复方案（已实施方案 A，符合设计原则）**：

让 userscript 只返回原始标记计数
（`{success_count: N, duplicate_count: N, error_count: N}`），后端用
`_classify_communication_markers()` 做分类决策。具体改动：

1. `userscript_channel.py` — `InstructionResult` 新增 `marker_counts` 字段
2. `schemas/userscript_bridge.py` — `ResultIn` 新增 `marker_counts` 字段
3. `api/v1/userscript_bridge.py` — `post_result()` 透传 `marker_counts`
4. `boss-userscript.user.js` — `read_communication_result` op 返回
   `result.marker_counts` 对象而非分类字符串
5. `userscript_adapter.py` — `read_communication_result()` 返回
   `dict[str, int] | None`；新增 `_classify_communication_markers()` 函数；
   `execute_communication()` 用其替代 `_communication_result_from_text()`
6. `classifiers.py` — `classify_communication_result()` 顺序修正为
   duplicate → success → error（与 B2 修复同步）

---

## 2. 测试覆盖现状分析

### 2.1 后端 Python 测试（758 通过）

| 层级 | 测试文件 | 已覆盖场景 | 缺口 |
|------|---------|-----------|------|
| **适配器层** | `test_userscript_boss_adapter.py` | disconnected → unknown; wrong page → unknown (无点击); immediate button missing → failed; message input missing → failed; page hash changed after fill → unknown (不发送); send failure → failed; success (恰好 1 immediate + 1 send); duplicate; unknown; channel clear after success; channel clear after failure; **duplicate 优先于 success**（B2 回归）; **platform_failure**（B1+B3）; **read_communication_result 指令失败 → unknown** | ① `set_active_application` 冲突（`active_conflict`）未测 ② `RuntimeError` catch-all（`runtime_error`）未测 |
| **服务层** | `test_boss_communicate_service.py` | prepare happy path; timeline event; source_snapshot 内容; reuse non-terminal action; payload_hash 敏感性; cross-user 404; missing resume 404; artifact not found 404; wrong artifact type 404; skip/needs_review/None opening_message → 422; execute unapproved → not_approved; approved + succeeded → submitted; duplicate; failed; unknown; no-adapter-before-approval; idempotency replay; payload_hash_mismatch; source_stale; cross-user 404; wrong action type 404 | 无重大缺口 |
| **API 层** | `test_boss_communicate_api.py` | prepare valid → 201; cross-user → 404; missing resume_version_id → 422; missing match_artifact_id → 422; skip decision → 422; execute approved → 200 (submitted); unapproved → 409; idempotent replay → 200; payload_hash_mismatch → 409; cross-user → 404 | 可补充：failed/unknown outcome 的 API 响应测试 |
| **Bridge 层** | `test_userscript_bridge_api.py` | page_id 过滤（tab-A 请求只取 tab-A 指令）; 无 page_id 回退（返回任意指令）; 不匹配重新入队（tab-B 指令不被 tab-A 取走）; 无绑定指令对任何 page_id 可用 | 无重大缺口 |
| **Channel 单元** | `test_userscript_channel.py` | singleton; reset; heartbeat connected/disconnected/stale; put/take FIFO; result timeout → failure; `set_active_application` 规则; `clear` 行为; text/url/error 脱敏; page-id binding match/mismatch/requeue | 无重大缺口 |

### 2.2 JS 层测试（零覆盖）

`querySelectorAllWithTextFilter()` 和 `read_communication_result` op 没有任何
自动化测试。所有后端测试通过 `FakeUserscriptChannel` 注入预定义的 `InstructionResult`，
从不经过真实 DOM 查询逻辑。

**项目现状**：

- 无根级 `package.json`，无 JS 测试框架（vitest/jest/mocha）
- `frontend/package.json` 只有 dev/build/lint/type-check，无 test script
- 设计文档（`design.md` 第 113 行）明确说"Browser script logic is hard to unit
  test → backend services/adapters are testable"，有意将可测试逻辑留在后端
- Node.js v22 可用，`npx` 可用

**方案分析**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A（推荐）** | 创建 `docs/boss-userscript-tests.html` — 独立 HTML 测试页，内嵌 `querySelectorAllWithTextFilter` 源码 + 构造的 DOM + 断言，开发者用浏览器打开即可验证 | 零依赖、无需安装、可视化、直接在真实浏览器 DOM 上运行 | 非自动化、不纳入 CI |
| **B** | 在 frontend/ 安装 vitest + jsdom，为 userscript 纯函数写正式单测 | 自动化、纳入 CI | 需引入新依赖、需 stub `GM_xmlhttpRequest`、与项目"userscript 保持简洁"哲学冲突 |
| **C** | 不做 JS 测试，靠后端 Python 测试 + 真实页面手动 runbook | 零成本 | JS 逻辑（尤其 `:has-text()` 处理）无任何验证，P1 bug 就是 JS 层问题 |

**推荐方案 A**，设计 12 个测试用例：

| # | 用例 | 输入 | 预期结果 | 验证目标 |
|---|------|------|---------|---------|
| J1 | 单个 `:has-text()` 匹配 | `.btn:has-text('继续沟通')` + DOM 含 `<button class="btn">继续沟通</button>` | 返回 1 个元素 | 基本功能 |
| J2 | `:has-text()` 不匹配 | 同上但按钮文本是"投递简历" | 返回 0 个元素 | 文本过滤 |
| J3 | 逗号分隔混合列表 | `.btn:has-text('已发送'), .status` + DOM 各有一个 | 返回 2 个元素 | 多选择器 |
| J4 | 无 `:has-text()` 纯 CSS 透传 | `.error-tip, .upload-error` + DOM 各有一个 | 返回 2 个元素 | 透传不变 |
| J5 | 清理后 CSS 为空 | `:has-text('x')`（无 CSS 前缀） | 返回空数组（不抛异常） | 边界安全 |
| J6 | 多个 `:has-text()` AND 语义 | `.btn:has-text('a'):has-text('b')` + DOM 含 `ab` 和 `a` | 仅匹配含 `ab` 的元素 | AND 语义 |
| J7 | 真实 SUCCESS_MARKER | 完整 `COMMUNICATION_SUCCESS_MARKER` + 模拟 BOSS 聊天 DOM | 成功匹配 | 真实选择器 |
| J8 | 真实 DUPLICATE_MARKER | 完整 `COMMUNICATION_DUPLICATE_MARKER` + 模拟"继续沟通"按钮 | 成功匹配 | 真实选择器 |
| J9 | **B1 bug 验证** | userscript 的 `.error-message` vs selectors.py 的 `.error-tip` — 同一 DOM，两个选择器分别查询 | 演示不一致 | 回归防护 |
| J10 | **B2 bug 验证** | 同时存在 success + duplicate 标记 | userscript 返回 `duplicate_detected`（反转） | 回归防护 |
| J11 | 空输入 | `""` | 返回空数组 | 边界安全 |
| J12 | 双引号变体 | `:has-text("已发送")` | 正常匹配 | 引号兼容 |

### 2.3 测试覆盖矩阵总览

```
                        适配器层    服务层    API层    Bridge层    JS层
disconnected            ✅          ✅        ✅       ✅          ❌
wrong page              ✅          ✅        ✅       ✅          ❌
button missing          ✅          ✅        -        -           -
input missing           ✅          ✅        -        -           -
hash changed            ✅          ✅        -        -           -
send failure            ✅          ✅        -        -           -
success                 ✅          ✅        ✅       -           ❌
duplicate               ✅          ✅        -        -           ❌
unknown                 ✅          ✅        -        -           ❌
platform_failure        ✅          ✅(fake)  -        -           ❌
dup > success priority  ✅          -         -        -           ❌
idempotency replay      ✅          ✅        ✅       -           -
payload_hash_mismatch   ✅          ✅        ✅       -           -
source_stale            ✅          ✅        -        -           -
page_id filtering       -           -         -        ✅          -
active_conflict        ❌(gap)     -         -        -           -
runtime_error          ❌(gap)     -         -        -           -
read_result failure    ✅          -         -        -           -
```

---

## 3. 真实 BOSS 页面验证步骤

> **前置条件**：P1/P2 修复已部署、B1 已修复（否则 `platform_failure` 和 error 路径
> 无法验证）、油猴脚本已安装并连接。

### 阶段 1：连接验证

- [ ] 后端已启动（`uvicorn app.main:app`）
- [ ] `export BOSS_USERSCRIPT_BRIDGE_ENABLED=1` 已设置
- [ ] 在 Chrome 中打开 `https://www.zhipin.com/`，登录 BOSS
- [ ] 浏览器控制台输出 `[boss-bridge] userscript loaded on https://www.zhipin.com/...`
- [ ] `curl http://127.0.0.1:8000/api/v1/userscript-bridge/status` → `{"connected": true, ...}`
- [ ] 前端「平台引导投递」面板显示「已连接」（绿色 Tag）

### 阶段 2：JD 读取验证

- [ ] 手动导航到一个**职位详情页**（如 `https://www.zhipin.com/job_detail/xxx.html`）
- [ ] 调用 inspect 端点：
  ```bash
  curl -X POST http://localhost:8000/api/v1/boss/recommended-jobs/current/inspect \
    -H "X-User-Id: <user_id>"
  ```
- [ ] 确认 `read_jd` 返回非空 JD 文本（`title`、`company`、`description` 非空）
- [ ] 确认 `page_url_hash` 与当前页面 URL 经 `sanitize_url()` 处理后一致
- [ ] 确认 JD 文本经过清洗（无 `<script>` 标签、无 HTML 实体编码残留）
- [ ] 确认数据库中 `Job` 和 `ApplicationRecord` 已创建

### 阶段 3：匹配决策验证

- [ ] 确认 `boss_match_decision` artifact 已生成
- [ ] 确认 `decision == "communicate"`
- [ ] 确认 `opening_message` 非空且通过人工审阅
- [ ] 确认 `opening_message` 通过 `validate_opening_message`：
  - 长度在范围内（无过短/过长）
  - 无手机号/邮箱/身份证号
  - 无过多标点符号

### 阶段 4：prepare + 审批验证

- [ ] 调用 prepare：
  ```bash
  curl -X POST \
    http://localhost:8000/api/v1/boss/recommended-jobs/<job_id>/communicate/prepare \
    -H "Content-Type: application/json" \
    -H "X-User-Id: <user_id>" \
    -d '{"resume_version_id": "<rv_id>", "match_artifact_id": "<ma_id>"}'
  ```
- [ ] 确认 `boss_immediate_communicate` action 已创建
- [ ] 确认 action 状态为 `approval_required`
- [ ] 确认 `payload_hash` 以 `sha256:` 开头
- [ ] 确认 `idempotency_key` 格式为 `{application_id}:boss_immediate_communicate:sha256:...`
- [ ] 确认 `source_snapshot` 包含 `job_url_hash` 和 `decision_trace`
- [ ] 确认 `source_snapshot` **不**包含 `opening_message`（敏感字段）
- [ ] 确认 timeline 记录了 `boss_communicate_previewed` 事件
- [ ] 手动审批：
  ```bash
  curl -X POST \
    http://localhost:8000/api/v1/applications/<app_id>/actions/<action_id>/approve \
    -H "X-User-Id: <user_id>"
  ```
- [ ] 确认 action 状态变为 `approved`

### 阶段 5：execute 验证（核心）

> ⚠️ **此步骤会真实发送消息给 BOSS HR。** 确认目标岗位是你不在意结果的非关键投递。

- [ ] 调用 execute：
  ```bash
  curl -X POST \
    http://localhost:8000/api/v1/boss/recommended-jobs/<job_id>/communicate/<action_id>/execute \
    -H "Content-Type: application/json" \
    -H "X-User-Id: <user_id>" \
    -d '{"application_id": "<app_id>"}'
  ```
- [ ] 确认油猴脚本在目标页面执行了以下指令（通过后端日志或浏览器控制台）：
  - 恰好 1 次 `click_immediate_communicate`（点击"立即沟通"按钮）
  - 恰好 1 次 `fill_opening_message`（填充开场白消息）
  - 恰好 1 次 `send_opening_message`（点击"发送"按钮）
  - 恰好 1 次 `read_communication_result`（读取结果标记）
- [ ] **关键验证**：`read_communication_result` 返回值**不是**总是 `unknown`
  - 如果总是 `unknown`，说明选择器未匹配真实 DOM → 检查 B1 是否已修复 +
    检查 `selectors.py` 中的 `COMMUNICATION_*` 选择器是否与真实页面结构一致
- [ ] 确认 `external_result_status` 与 `read_communication_result` 返回值一致：

  | userscript 返回 | external_result_status | 含义 |
  |-----------------|----------------------|------|
  | `succeeded` | `submitted` | 消息已发送 |
  | `duplicate_detected` | `duplicate` | 对话已存在 |
  | `platform_failure` | `failed` | 平台错误（B1 修复后才可验证） |
  | `unknown` | `unknown` | 模糊状态（硬停止） |

- [ ] 确认 `active_application_id` 已清空（`GET /userscript-bridge/status` → `active_application_id: null`）
- [ ] 确认结果中无 cookie / token / 原始 HTML / 原始消息内容
- [ ] 确认 timeline 记录了对应的终态事件（`boss_communicate_succeeded` /
      `boss_communicate_duplicate` / `boss_communicate_failed` / `boss_communicate_unknown`）

### 阶段 6：幂等验证

- [ ] 对同一 action **再次**调用 execute
- [ ] 确认返回 **200**（非 409）
- [ ] 确认 `external_result_status` 保持终态值不变
- [ ] 确认响应消息包含「幂等重放」前缀
- [ ] 确认油猴脚本**未被再次调用**（后端日志无新指令、`communicate_calls` 不增加）
- [ ] 确认 timeline 记录了 `boss_communicate_blocked` 事件，`reason=idempotency_replay`

### 阶段 7：多 tab 安全验证

- [ ] 在 Chrome 中打开**两个** BOSS 职位详情页 tab（不同职位）
- [ ] 确认两个 tab 的 `page_id` 不同（浏览器控制台日志中的 `PAGE_ID` 值）
- [ ] 确认 `GET /userscript-bridge/heartbeat` 返回的 `page_id` 是最后心跳的 tab
- [ ] 对 tab-A 对应的 action 调用 execute
- [ ] 确认只有 tab-A 的油猴脚本执行了指令（tab-B 控制台无指令执行日志）
- [ ] 确认 tab-B 不会 dequeue 属于 tab-A 的指令（page_id 过滤生效）
- [ ] 如果 tab-B 先发起了 `GET /next-instruction?page_id=tab-B`，确认 tab-A 的指令
      被重新入队，tab-A 随后可以取到

### 验证记录要求

每次执行真实验证后，记录以下信息到 `check.jsonl`（或开发日志）：

- 日期 + 操作者
- 目标职位 URL（脱敏为 hash）
- `read_communication_result` 实际返回值
- `external_result_status`
- 是否有异常（选择器漂移、超时、意外弹窗等）
- 人工对账结论（如果 `unknown`，登录 BOSS 确认消息是否已发送）

---

## 4. 后续路线图

### P0 — 验收前必须修复

| # | 工作项 | 文件 | 说明 |
|---|--------|------|------|
| P0-1 | ✅ 已修复 B1：油猴脚本 error 选择器 | `docs/boss-userscript.user.js` 第 604-605 行 | `.error-message, .toast-error, .dialog-error` → `.error-tip, .error-content, .upload-error`，与 `PLATFORM_ERROR_MARKER` 一致 |
| P0-2 | 执行阶段 1-7 真实页面验证 | — | 至少 3 次 `succeeded` + 1 次 `duplicate_detected` 场景 |

### P1 — 生产前应该修复

| # | 工作项 | 文件 | 说明 |
|---|--------|------|------|
| P1-1 | ✅ 已修复 B2：统一分类优先级 | `classifiers.py` 第 185-196 行 + `selectors.py` 第 172-182 行 | Python classifier 改为 duplicate → success → error；在 SUCCESS_MARKER 注释中明确"继续沟通"是 duplicate marker |
| P1-2 | ✅ 已修复 B3：`classify_communication_result` 接入 | `classifiers.py` + `userscript_adapter.py` + `userscript_channel.py` + `schemas/userscript_bridge.py` + `api/v1/userscript_bridge.py` + `boss-userscript.user.js` | 实施方案 A：userscript 返回 `marker_counts`，后端用 `_classify_communication_markers()` 做分类决策 |
| P1-3 | ✅ 已补充适配器层缺失测试 | `test_userscript_boss_adapter.py` | 新增 3 个测试：`test_communicate_duplicate_priority_over_success`、`test_communicate_platform_failure`、`test_communicate_read_result_failure_returns_unknown` |
| P1-4 | 创建 JS 测试页 | `docs/boss-userscript-tests.html` | 实现方案 A，跑通 J1-J12 |

### P2 — 健壮性增强

| # | 工作项 | 说明 |
|---|--------|------|
| P2-1 | 补充 API 层 failed/unknown outcome 响应测试 | 当前 API 层只测了 `submitted`，未测 `failed`/`unknown` 的 HTTP 响应 |
| P2-2 | ✅ 已完成：userscript 选择器改为后端下发 | `Instruction` 新增 `extra_selectors` 字段，`read_communication_result` 携带 success/duplicate/error 3 组选择器，`send_opening_message` 携带 `message_input` 选择器；userscript 从 `ins.extra_selectors` 读取并 fallback 到硬编码（向后兼容）。消除 B1 根因。 |
| P2-3 | ✅ 已完成：分类逻辑收归后端 | userscript 返回 `{success_count, duplicate_count, error_count}`，后端用 `_classify_communication_markers()` 做分类决策（符合"后端 agent 决策，油猴执行"原则）。即 B3 修复。 |
| P2-4 | 选择器漂移自动检测 | 定期或在 prepare 阶段检查关键选择器是否匹配，提前预警 |

### P3 — 功能扩展

| # | 工作项 | 说明 |
|---|--------|------|
| P3-1 | 前端 `RecommendedJobPilotPanel` 组件 | `design.md` 第 45-51 行设计了但未实现；包含 bridge status、JD preview、match decision、opening message preview、approve/execute controls |
| P3-2 | 推荐列表大循环 | `design.md` Rollout Plan step 7；当前只做单职位闭环，不做推荐列表批量处理 |
| P3-3 | `RealBossAdapter.execute_communication()` 实现 | 当前 communicate 只有 fake（测试）和 userscript（真实）路径，无 CDP fallback。如果 userscript 路径不可用且 CDP 可用，需要 fallback |
| P3-4 | 10 次 dry-run 门槛 | `design.md` Rollout Plan step 6 要求"至少 10 次真实 dry-run 无 wrong-tab/duplicate/unknown 事故"后才可启用 auto-execute |

---

## 5. 验收标准汇总

对照设计文档（`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`）
中的需求和 Rollout Plan：

| 验收标准 | 当前状态 | 备注 |
|---------|---------|------|
| **R1**: `read_jd` 可读取职位 JD | ✅ 已实现 + 测试 | 需真实页面验证（阶段 2） |
| **R2**: 后端 match decision | ✅ 已实现 + 测试 | 需真实 model 验证（阶段 3） |
| **R3**: 可审计/幂等 communicate action | ✅ 已实现 + 测试 | prepare + execute + idempotency 全链路 |
| **R4**: page binding + 多 tab 安全 | ✅ 已实现 + 测试 | P2 修复后需重新验证（阶段 7） |
| **R5**: 最小 userscript executor | ✅ 已实现 | B1/B2/B3 bug 均已修复 |
| **R6**: failure-first 硬停止 | ✅ 已实现 | B1 修复后 `platform_failure` 路径在真实页面可正确触发 |
| **安全不变量**: 至多 1 click + 1 send | ✅ 已实现 + 测试 | `assert communicate_click_count <= 2` |
| **安全不变量**: unknown 不自动重试 | ✅ 已实现 + 测试 | |
| **安全不变量**: 无原始 HTML/cookie/token 持久化 | ✅ 已实现 + 测试 | |
| **安全不变量**: page hash 点击前+填充后各验证 | ✅ 已实现 + 测试 | |
| **安全不变量**: channel 每次执行后清空 | ✅ 已实现 + 测试 | |
| **Rollout**: 至少 10 次 dry-run 无事故 | ❌ 未执行 | 需完成阶段 1-7 后积累 |
| **Rollout**: 无 wrong-tab 事故 | ❌ 未验证 | 阶段 7 验证 |
| **Rollout**: 无 duplicate 误发 | ❌ 未验证 | 阶段 5 验证 |
| **Rollout**: 无 unknown 误停 | ❌ 未验证 | 阶段 5 验证 |

### 验收通过条件

1. ✅ B1 已修复（P0-1）
2. ✅ B2 已修复（P1-1）
3. ✅ B3 已修复（P1-2，方案 A）
4. 阶段 1-7 全部通过，至少 3 次 `succeeded` + 1 次 `duplicate_detected`
5. 无 wrong-tab / duplicate 误发 / unexpected unknown 事故
6. 所有 758+ 后端测试持续通过
7. JS 测试页 J1-J12 通过（P1-4，可与验收并行）
8. 验证记录已归档到 `check.jsonl` 或开发日志

---

## 附录：相关文件索引

| 文件 | 说明 |
|------|------|
| `docs/boss-userscript.user.js` | 油猴用户脚本（页面内执行器） |
| `docs/manual-boss-pilot.md` | 手动试点 runbook（§8 为沟通试点） |
| `docs/boss-anti-automation-findings.md` | BOSS 反自动化检测调研报告 |
| `backend/app/platforms/boss/selectors.py` | BOSS 选择器定义 |
| `backend/app/platforms/boss/classifiers.py` | 页面状态分类器（含 `classify_communication_result`，已接入 userscript 路径） |
| `backend/app/platforms/boss/userscript_adapter.py` | 油猴桥接适配器 |
| `backend/app/platforms/boss/userscript_channel.py` | 油猴桥接通道（指令队列） |
| `backend/app/services/boss_communicate_service.py` | 沟通服务层（prepare + execute guard chain） |
| `backend/app/api/v1/boss_communicate.py` | 沟通 API 端点 |
| `backend/app/tests/test_userscript_boss_adapter.py` | 适配器层测试 |
| `backend/app/tests/test_boss_communicate_service.py` | 服务层测试 |
| `backend/app/tests/test_boss_communicate_api.py` | API 层测试 |
| `backend/app/tests/test_userscript_bridge_api.py` | Bridge 层测试（含 page_id 过滤） |
| `.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md` | 设计文档（含 Failure Matrix + Rollout Plan） |
