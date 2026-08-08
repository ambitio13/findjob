# RealBossAdapter.execute_communication() CDP fallback Design

## Scope

实现 `RealBossAdapter.execute_communication()`，作为 userscript bridge 不可用时的 CDP/Playwright
fallback。它是保底路径，不是推荐真实生产路径；真实 BOSS 优先使用 userscript。

## Adapter Selection

现有 registry 已按 userscript → real → fake 优先级选择适配器：

- `BOSS_USERSCRIPT_BRIDGE_ENABLED=1`：使用 `UserscriptBossAdapter`。
- 否则 `BOSS_ADAPTER_ENABLED=1`：使用 `RealBossAdapter`。
- 否则使用 `FakeBossAdapter`。

本任务不需要改变优先级。只需要让 real adapter 满足 `PlatformAdapter.execute_communication`
合同。

## CDP Constraints

依据 `docs/boss-anti-automation-findings.md`：

- CDP 模式必须连接用户已经打开、已经登录的真实 Chrome。
- CDP 模式不得调用 `page.goto`。
- 用户必须手动导航到目标职位页面。
- 断开时只断开 CDP client，不关闭用户 Chrome/tab。

这些约束已经在 `BossBrowserRuntime` 中部分实现，`execute_communication()` 必须沿用。

## Flow

1. 读取 `boss_session_profile_dir` / `boss_cdp_endpoint`，缺失则返回 `unknown` +
   `missing_session_config`。
2. 打开 runtime，获取当前 `BossPage`。
3. 校验当前页面 hash 与 `ctx.target_resource` 一致。
4. 探测 `IMMEDIATE_COMMUNICATE_BUTTON` 与 `CONTINUE_COMMUNICATE_BUTTON`：
   - continue 可见：返回 `duplicate`。
   - 两者都不可见：返回 `failed` + `selector_drift`。
5. 点击 `IMMEDIATE_COMMUNICATE_BUTTON` 最多一次。
6. 填充 `COMMUNICATION_MESSAGE_INPUT`。
7. 再次校验页面 hash 未变化。
8. 点击 `COMMUNICATION_SEND_BUTTON` 最多一次。
9. 调用 `classify_communication_result(page)` 得出 duplicate/succeeded/platform_failure/unknown。
10. 映射到 `CommunicationExecuteResult`。

## Safety Invariants

- 最多 1 次立即沟通点击 + 1 次发送点击。
- page hash 点击前和填充后都验证。
- login/captcha/rate-limit/selector-drift 是硬停止。
- unknown 不自动重试。
- 不持久化 raw HTML、cookie、token、完整 URL 或未脱敏诊断。

## Tests

使用现有 fake Playwright/runtime pattern，不连接真实浏览器。至少覆盖：

- missing session config → unknown。
- wrong page hash → unknown，不点击。
- continue button visible → duplicate，不发送。
- both entry buttons invisible → failed selector_drift。
- immediate visible happy path → succeeded。
- fill failure / send failure → failed。
- post-send classifier unknown → unknown。
- click budget 至多一次。

## Compatibility

不改变 userscript path。`UserscriptBossAdapter` 仍是推荐真实路径；CDP fallback 只在配置选择到
`RealBossAdapter` 时生效。
