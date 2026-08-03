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

- [ ] 后端已启动（`uvicorn app.main:app`）
- [ ] `export BOSS_USERSCRIPT_BRIDGE_ENABLED=1` 已设置
- [ ] Chrome 中打开 `https://www.zhipin.com/`，登录 BOSS
- [ ] 浏览器控制台输出 `[boss-bridge] userscript loaded on https://www.zhipin.com/...`
- [ ] `curl http://127.0.0.1:8000/api/v1/userscript-bridge/status` → `{"connected": true, ...}`
- [ ] 前端「平台引导投递」面板显示「已连接」（绿色 Tag）

### R2. 阶段 2 — JD 读取验证

- [ ] 手动导航到一个职位详情页
- [ ] 调用 inspect 端点，确认 `read_jd` 返回非空 JD（`title`、`company`、`description` 非空）
- [ ] 确认 `page_url_hash` 与当前页面 URL 经 `sanitize_url()` 处理后一致
- [ ] 确认 JD 文本经过清洗（无 `<script>` 标签、无 HTML 实体编码残留）
- [ ] 确认数据库中 `Job` 和 `ApplicationRecord` 已创建

### R3. 阶段 3 — 匹配决策验证

- [ ] 确认 `boss_match_decision` artifact 已生成
- [ ] 确认 `decision == "communicate"`
- [ ] 确认 `opening_message` 非空且通过人工审阅
- [ ] 确认 `opening_message` 通过 `validate_opening_message`（长度合规、无手机号/邮箱/身份证号、无过多标点）

### R4. 阶段 4 — prepare + 审批验证

- [ ] 调用 prepare 端点
- [ ] 确认 `boss_immediate_communicate` action 已创建，状态 `approval_required`
- [ ] 确认 `payload_hash` 以 `sha256:` 开头
- [ ] 确认 `idempotency_key` 格式为 `{application_id}:boss_immediate_communicate:sha256:...`
- [ ] 确认 `source_snapshot` 包含 `job_url_hash` 和 `decision_trace`
- [ ] 确认 `source_snapshot` **不**包含 `opening_message`（敏感字段）
- [ ] 确认 timeline 记录了 `boss_communicate_previewed` 事件
- [ ] 手动审批，确认 action 状态变为 `approved`

### R5. 阶段 5 — execute 验证（核心）

> ⚠️ 此步骤会真实发送消息给 BOSS HR。确认目标岗位是非关键投递。

- [ ] 调用 execute 端点
- [ ] 确认油猴脚本执行恰好 4 条指令（click_immediate / fill_message / send_message / read_result 各 1 次）
- [ ] **关键验证**：`read_communication_result` 返回值**不是**总是 `unknown`
- [ ] 确认 `external_result_status` 与 `read_communication_result` 返回值一致
- [ ] 确认 `active_application_id` 已清空
- [ ] 确认结果中无 cookie / token / 原始 HTML / 原始消息内容
- [ ] 确认 timeline 记录了对应的终态事件

### R6. 阶段 6 — 幂等验证

- [ ] 对同一 action 再次调用 execute
- [ ] 确认返回 200（非 409）
- [ ] 确认 `external_result_status` 保持终态值不变
- [ ] 确认油猴脚本未被再次调用
- [ ] 确认 timeline 记录了 `boss_communicate_blocked`，`reason=idempotency_replay`

### R7. 阶段 7 — 多 tab 安全验证

- [ ] 开两个 BOSS 职位详情页 tab（不同职位）
- [ ] 确认两个 tab 的 `page_id` 不同
- [ ] 对 tab-A 对应的 action 调用 execute
- [ ] 确认只有 tab-A 的油猴脚本执行了指令
- [ ] 确认 tab-B 不会 dequeue 属于 tab-A 的指令

## Acceptance Criteria

- [ ] 阶段 1-7 全部通过
- [ ] 至少 3 次 `succeeded` 场景
- [ ] 至少 1 次 `duplicate_detected` 场景
- [ ] 无 wrong-tab 事故
- [ ] 无 duplicate 误发
- [ ] 无 unexpected unknown 误停
- [ ] 验证记录已归档到 `check.jsonl` 或开发日志（含日期、操作者、脱敏 URL、返回值、`external_result_status`、异常情况、人工对账结论）

## Notes

- 详细步骤见 `docs/boss-communicate-testing-plan.md` 第 3 节。
- 前置条件：B1 已修复（否则 `platform_failure` 路径无法验证）。
- 这是手动验证任务，无代码产出。
