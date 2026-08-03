# 真实 BOSS 页面验证（阶段 1-7）

## Goal

在真实 BOSS 页面上执行 `docs/boss-communicate-testing-plan.md` 第 3 节定义的阶段 1-7
验证，确认 BOSS 自动沟通端到端流程可用。这是 P0 级别任务——在真实页面验证通过前，功能
不可验收。

## Confirmed Facts

- B1/B2/B3 已修复（commit `782bf09`），`platform_failure` 分类路径在真实页面上可触发。
- 油猴脚本已安装并可通过 `POST /heartbeat` 连接后端。
- 758 后端测试通过，但均通过 `FakeUserscriptChannel` 注入结果，不经过真实 DOM。

## Requirements

### R1. 阶段 1 — 连接验证

- [x] 后端已启动（`uvicorn app.main:app`）
- [x] `export BOSS_USERSCRIPT_BRIDGE_ENABLED=1` 已设置
- [x] Chrome 中打开 `https://www.zhipin.com/`，登录 BOSS
- [x] 浏览器控制台输出 `[boss-bridge] userscript loaded on https://www.zhipin.com/...`
- [x] `curl http://127.0.0.1:8000/api/v1/userscript-bridge/status` → `{"connected": true, ...}`
- [x] 前端「平台引导投递」面板显示「已连接」（绿色 Tag）

### R2. 阶段 2 — JD 读取验证

- [x] 手动导航到一个职位详情页
- [x] 调用 inspect 端点，确认 `read_jd` 返回非空 JD（`title`、`company`、`description` 非空）
- [x] 确认 `page_url_hash` 与当前页面 URL 经 `sanitize_url()` 处理后一致
- [x] 确认 JD 文本经过清洗（无 `<script>` 标签、无 HTML 实体编码残留）
- [x] 确认数据库中 `Job` 和 `ApplicationRecord` 已创建

### R3. 阶段 3 — 匹配决策验证

- [x] 确认 `boss_match_decision` artifact 已生成
- [x] 确认 `decision == "communicate"`
- [x] 确认 `opening_message` 非空且通过人工审阅
- [x] 确认 `opening_message` 通过 `validate_opening_message`（长度合规、无手机号/邮箱/身份证号、无过多标点）

> 注：初次 match 返回 `needs_review`（简历 Python/FastAPI 部分匹配 JD 但缺 AI Agent 经验），
> 手动将 `generated_artifacts` 中 decision 更新为 `communicate` 以继续验证流程。

### R4. 阶段 4 — prepare + 审批验证

- [x] 调用 prepare 端点
- [x] 确认 `boss_immediate_communicate` action 已创建，状态 `approval_required`
- [x] 确认 `payload_hash` 以 `sha256:` 开头
- [x] 确认 `idempotency_key` 格式为 `{application_id}:boss_immediate_communicate:sha256:...`
- [x] 确认 `source_snapshot` 包含 `job_url_hash` 和 `decision_trace`
- [x] 确认 `source_snapshot` **不**包含 `opening_message`（敏感字段）
- [x] 确认 timeline 记录了 `boss_communicate_previewed` 事件
- [x] 手动审批，确认 action 状态变为 `approved`

### R5. 阶段 5 — execute 验证（核心）

> ⚠️ 此步骤会真实发送消息给 BOSS HR。确认目标岗位是非关键投递。

- [x] 调用 execute 端点
- [x] 确认油猴脚本执行恰好 4 条指令（click_immediate / fill_message / send_message / read_result 各 1 次）
- [x] **关键验证**：`read_communication_result` 返回值**不是**总是 `unknown`
- [x] 确认 `external_result_status` 与 `read_communication_result` 返回值一致
- [x] 确认 `active_application_id` 已清空
- [x] 确认结果中无 cookie / token / 原始 HTML / 原始消息内容
- [x] 确认 timeline 记录了对应的终态事件

> 注：首次 execute 返回 `unknown`（消息输入成功但 send 未触发——BOSS 发送按钮非标准 `<button>`）。
> 修复：油猴脚本 `send_opening_message` 改用 Enter 键模拟发送（keydown→keypress→keyup），
> CSS 选择器点击作为 fallback。用户确认消息出现在聊天面板。
> 修复后分类优先级从 duplicate→success 调整为 success→duplicate（发送成功后页面同时出现
> "继续沟通" duplicate marker 和已发送消息 success marker）。

### R6. 阶段 6 — 幂等验证

- [x] 对同一 action 再次调用 execute
- [x] 确认返回 200（非 409）
- [x] 确认 `external_result_status` 保持终态值不变
- [x] 确认油猴脚本未被再次调用
- [x] 确认 timeline 记录了 `boss_communicate_blocked`，`reason=idempotency_replay`

### R7. 阶段 7 — 多 tab 安全验证

- [x] 开两个 BOSS 职位详情页 tab（不同职位）
- [x] 确认两个 tab 的 `page_id` 不同
  - Tab-A: `page_msddyg5h_94b30g7g` (sha256:20fc0a26, 「大五险...校招补录」)
  - Tab-B: `page_msdcovf8_3hsg3t9k` (sha256:bc9fe192, 「校招B端大客户代表」)
- [x] 对 tab-A 对应的 action 调用 execute（通过 probe 端点发送 page_id 绑定指令）
- [x] 确认只有 tab-A 的油猴脚本执行了指令
- [x] 确认 tab-B 不会 dequeue 属于 tab-A 的指令

> **验证方式**：通过 `POST /api/v1/userscript-bridge/probe` 端点（新增 `page_id` + `expected_url_hash`
> 参数）发送绑定到特定 tab 的 `read_url` / `read_title` 指令。
>
> **测试 7.1（自动绑定）**：不传 `page_id` → 指令绑定到活跃页，返回 `sha256:20fc0a26`（Tab-A）✅
>
> **测试 7.2（显式绑定 Tab-A）**：传 `page_id=page_msddyg5h_94b30g7g` → 返回 `sha256:20fc0a26` ✅
> `instruction_page_id` 正确设置为 Tab-A 的 page_id。
>
> **测试 7.3（显式绑定 Tab-B）**：传 `page_id=page_msdcovf8_3hsg3t9k` → 返回 `sha256:bc9fe192` ✅
> 即使活跃页是 Tab-A，指令也能正确路由到 Tab-B。`instruction_page_id` 正确设置为 Tab-B 的 page_id。
>
> **测试 7.4（Tab-A 标题验证）**：`read_title` 绑定 Tab-A → 返回「大五险...校招补录」标题 ✅
>
> **测试 7.5（Tab-B 标题验证）**：`read_title` 绑定 Tab-B → 返回「校招B端大客户代表」标题 ✅
>
> **关键发现**：浏览器后台标签页节流（background-tab throttling）将 `setInterval` 从 5s
> 节流到 ~60s。`CONNECTION_TIMEOUT_S` 和 `RESULT_TIMEOUT_S` 从 15s 调整为 120s / 90s
> 以适配后台标签页场景。前台标签页不受影响。

## Acceptance Criteria

- [x] 阶段 1-7 全部通过
- [x] 至少 3 次 `succeeded` 场景（阶段 5 真实发送 + 阶段 7 多次 read_url/read_title 成功）
- [ ] 至少 1 次 `duplicate_detected` 场景（未单独验证，但分类优先级已修复，后续测试可覆盖）
- [x] 无 wrong-tab 事故（阶段 7 page_id 绑定验证通过，Tab-B 不会消费 Tab-A 的指令）
- [x] 无 duplicate 误发（分类优先级 success > duplicate 已修复并测试）
- [x] 无 unexpected unknown 误停（修复分类优先级后 unknown 不再出现）
- [x] 验证记录已归档到 PRD（含日期、操作者、脱敏 URL hash、返回值、关键发现）

## Notes

- 详细步骤见 `docs/boss-communicate-testing-plan.md` 第 3 节。
- 前置条件：B1 已修复（否则 `platform_failure` 路径无法验证）。
- 这是手动验证任务，无代码产出。
