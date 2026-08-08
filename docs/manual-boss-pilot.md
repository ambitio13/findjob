# BOSS 平台真实适配器手动试点 Runbook

本 runbook 指导开发者如何在本地安全地试点真实 BOSS Web 适配器。系统支持
三种适配器（按优先级）：**油猴脚本桥接**（推荐）> **CDP/Playwright**（保留为
fallback）> **fake**（默认，测试/开发）。

**真实提交（submit）只能在至少一次 dry-run prepare 被人工检查通过后才可启用。**

## 模式 A：油猴脚本桥接（推荐）

通过 Tampermonkey 用户脚本在页面自身的 JS 上下文中执行后端指令，无 CDP 协议
签名，绕过 BOSS 的 CDP 协议层自动化检测（`Page.navigate` + `Runtime.evaluate`）。

### A.0 工作原理

- 后端构造每一条指令（op + selector + fill_value），油猴脚本只执行。
- 指令队列是**纯内存**（asyncio.Queue），不持久化到 Redis/DB。
- 油猴脚本通过 4 个无认证 HTTP 端点与后端通信：
  - `GET /userscript-bridge/status` — 查询连接状态
  - `GET /userscript-bridge/next-instruction` — long-poll 取指令（5s）
  - `POST /userscript-bridge/result` — 回传指令执行结果（已脱敏）
  - `POST /userscript-bridge/heartbeat` — 心跳维持连接
- 后端 `UserscriptBossAdapter` 实现与 `RealBossAdapter` 相同的 prepare/submit
  流程，但每一步操作都变成一次 HTTP 往返指令。
- 安全不变量全部保留：prepare 永不点击提交按钮、submit 恰好点击一次、回传结果
  只含脱敏值（sha256(url)、截断标题、stripped error）。

### A.1 安装油猴脚本

1. 安装 [Tampermonkey](https://www.tampermonkey.net/) 浏览器扩展。
2. 打开 Tampermonkey 管理面板 → 「新建脚本」。
3. 将 `docs/boss-userscript.user.js` 的全部内容粘贴进去，保存（Ctrl+S）。
4. 确认脚本的 `@match` 规则覆盖 `https://www.zhipin.com/*`。

### A.2 登录并打开目标页面

```bash
# 在真实 Chrome 中打开 BOSS 直聘
# 1. 登录 BOSS（手机验证码登录）
# 2. 手动导航到目标职位页面 / HR 会话页面
#    油猴脚本会在页面加载后自动开始心跳 + 轮询指令
```

油猴脚本不需要 CDP 调试端口，也不需要独立的 user-data-dir。它直接运行在你
日常使用的 Chrome 标签页中。

### A.3 启用环境标志

```bash
export BOSS_USERSCRIPT_BRIDGE_ENABLED=1
# 可选：同时保留 CDP 作为 fallback（但 userscript 优先级更高）
# export BOSS_ADAPTER_ENABLED=1
# export BOSS_CDP_ENDPOINT=http://127.0.0.1:9222
```

- `BOSS_USERSCRIPT_BRIDGE_ENABLED=1`：切换 registry 到油猴桥接适配器。
  当此标志设置时，**优先于** `BOSS_ADAPTER_ENABLED`（三路选择：userscript >
  real > fake）。
- 油猴脚本默认连接 `http://127.0.0.1:8000/api/v1`。如果你的后端端口不同，
  修改 `docs/boss-userscript.user.js` 中的 `BACKEND_BASE`。

> **注意**：油猴脚本通过 `GM_xmlhttpRequest` 跨域请求后端，不受浏览器同源策略
> 限制。`@connect` 已限制为仅允许 `localhost` / `127.0.0.1`。

### A.4 验证连接

1. 确保后端已启动（`uvicorn app.main:app`）。
2. 在 BOSS 直聘页面打开浏览器控制台，应看到：
   ```
   [boss-bridge] userscript loaded on https://www.zhipin.com/...
   ```
3. 在前端「平台引导投递」面板中，油猴桥接状态应显示「已连接」（绿色 Tag）。
4. 或直接调用状态端点：
   ```bash
   curl http://127.0.0.1:8000/api/v1/userscript-bridge/status
   # {"connected": true, "last_heartbeat": "...", "active_application_id": null}
   ```

### A.5 Dry-run prepare + 真实提交

与模式 B 的步骤 4–6 完全相同（见下文）。唯一的区别是适配器实现：
`UserscriptBossAdapter` vs `RealBossAdapter`。两者实现相同的
`PlatformAdapter` 协议，所以前端流程、审批边界、幂等键检查完全不变。

### A.6 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 前端显示「未连接」 | 油猴脚本未运行 / 后端未启动 | 确认脚本已启用、后端已启动、页面在 zhipin.com |
| prepare 返回 `unknown` | 连接超时（15s 无心跳） | 刷新 BOSS 页面，等待脚本重新连接 |
| prepare 返回 `selector_drift` | BOSS 页面结构变化 | 更新 `selectors.py` |
| 指令执行失败 | 选择器在页面上找不到元素 | 确认你导航到了正确的职位/HR 页面 |

---

## 模式 B：CDP/Playwright（保留为 fallback）

> ⚠️ BOSS 直聘已检测到 CDP 协议层自动化。此模式在生产环境中可能被风控拦截，
> 仅在油猴方案不可用时作为 fallback 使用。

### B.1 创建本地 BOSS 会话（CDP 连接真实 Chrome）

BOSS 的风控非常激进，会从多个维度检测自动化：

1. **Playwright 启动的浏览器**（`launch_persistent_context`，即使加了
   `--disable-blink-features=AutomationControlled`）→ 页面返回 `about:blank`
   或在登录/职位页之间反复重定向。
2. **CDP 连接真实 Chrome + `page.goto` 导航到 `/web/geek/*`** → 页面被 BOSS
   前端 JS 销毁为 `about:blank`（即使浏览器指纹是 100% 真实 Chrome）。
3. **CDP 连接 + `Runtime.evaluate` 调用** → BOSS 检测到 JS 执行请求后
   主动关闭页面（~0.5s 延迟）。
4. `add_init_script` 反检测脚本（隐藏 `navigator.webdriver` 等）→ **无效**。
   BOSS 的检测不依赖 `navigator.webdriver`。

**唯一可行的方式**：通过 CDP 连接真实 Chrome，但**永不**调用 `page.goto`。
用户在 Chrome 窗口中手动导航到目标页面，适配器通过 CDP 只做读取和点击操作。

#### 步骤

```bash
# 1. 关闭所有已打开的 Chrome 窗口（必须，否则 --remote-debugging-port 不生效）

# 2. 用调试端口 + 独立 user-data-dir 启动真实 Chrome
#    （--remote-debugging-port 要求非默认 user-data-dir）
mkdir -p ~/.boss-debug-chrome
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.boss-debug-chrome" \
  &>/dev/null &

# 3. 在打开的 Chrome 窗口中手动登录 BOSS（手机验证码登录）
#    打开 https://www.zhipin.com/web/geek/job-recommend 确认已登录

# 4. 手动导航到目标职位页面 / HR 会话页面
#    适配器不会自动导航 —— 它复用你当前打开的页面
```

登录成功后，Chrome 保持运行并停留在目标页面即可。适配器通过 CDP 连接到
这个 Chrome 实例，复用你已登录的会话和当前打开的页面，断开连接时**不会**
关闭你的 Chrome 窗口。

> **安全提示**：`~/.boss-debug-chrome` 目录等价于你的登录态，请勿提交到 git
> 或共享。`.gitignore` 已包含相关模式。

> **BOSS 反自动化注意事项**：
> - 适配器**不会**调用 `page.goto` —— BOSS 会检测 CDP 导航并销毁页面。
> - 适配器通过 CDP 读取 DOM 和点击元素，但 `Runtime.evaluate` 调用需尽量少
>   （BOSS 会在多次 evaluate 后关闭页面）。
> - 如果页面被 BOSS 关闭，适配器返回 `unknown`（硬停止），需手动重新打开页面。

### B.2 启用环境标志

```bash
# 确保未设置 BOSS_USERSCRIPT_BRIDGE_ENABLED（否则会优先走油猴模式）
unset BOSS_USERSCRIPT_BRIDGE_ENABLED

export BOSS_ADAPTER_ENABLED=1
export BOSS_CDP_ENDPOINT=http://127.0.0.1:9222
# 可选：CDP 未设置时回退到 persistent context 方式（测试/CI 用）
# export BOSS_SESSION_PROFILE_DIR=/Users/<you>/.boss-pilot-profile
```

- `BOSS_ADAPTER_ENABLED=1`：切换 registry 从 fake 适配器到真实 CDP 适配器。
- `BOSS_CDP_ENDPOINT`：指向步骤 B.1 启动的 Chrome 调试端口（推荐方式）。
- `BOSS_SESSION_PROFILE_DIR`：CDP 端点未设置时的 fallback（persistent context
  方式，可能被 BOSS 风控拦截，仅用于测试/CI）。

未设置 `BOSS_CDP_ENDPOINT` 和 `BOSS_SESSION_PROFILE_DIR` 时，系统返回
`login_required`（所有测试/开发默认路径不受影响）。

## 3. 选择非关键投递记录

在数据库中选一条状态为 `ready` 或 `prepared` 的 application record，确认：

- 该岗位你**不在意**投递结果（用于试点验证，不是真实求职目标）。
- `target_resource` 是一个有效的 BOSS 职位/HR 会话 URL。
- `outgoing_text` 已生成且经过人工审阅。

## 4. Dry-run prepare（stop-before-submit 验证）

```bash
# 调用 prepare 端点（ enqueue + poll ）
curl -X POST http://localhost:8000/api/v1/applications/<application_id>/platform-submissions/prepare \
  -H "Content-Type: application/json" \
  -d '{"target_resource":"https://www.zhipin.com/job/xxx","outgoing_text":"您好，我对这个岗位很感兴趣。"}'
```

轮询 `GET /api/v1/agent-runs/<run_id>/detail` 直到 run 进入 `succeeded`。

### 验证清单

- [ ] run 状态为 `succeeded`，action 的 `external_result` 包含 `filled_preview`。
- [ ] `FilledSubmissionSnapshot` 的 `page_state.final_submit_selector_seen=true`。
- [ ] **`page_state` 中无 cookie / token / 原始 HTML / profile 路径。**
- [ ] `url_hash` 形如 `sha256:xxxxxxxx`，**非**原始 URL。
- [ ] 适配器日志中 `boss.adapter.prepare` 记录的 `target_resource` 是 hash，非原始 URL。
- [ ] 浏览器已关闭（无残留进程）。

## 5. 失败场景捕获

如果 prepare 返回非 `filled_preview`，检查 `latest_error` / timeline 中的
`failure_code`：

| failure_code                  | 含义                         | 处理                              |
| ----------------------------- | ---------------------------- | --------------------------------- |
| `platform_login_required`     | profile 未登录 / 会话过期     | 重新执行步骤 1 登录               |
| `platform_captcha_required`   | BOSS 弹出验证码              | 手动登录解决；**不**自动绕过      |
| `platform_rate_limited`       | 账号被限流                   | 等待限流解除后重试                |
| `platform_duplicate_detected` | 已投递/已沟通                | 正常，无需重试                    |
| `platform_selector_drift`     | BOSS 页面结构变化            | 更新 `selectors.py` 后重试        |
| `platform_upload_failed`      | 简历上传失败                 | 检查 resume 文件引用，可重试      |
| `platform_unknown_result`     | 模糊状态                     | 人工介入，检查浏览器日志          |

> **硬停止原则**：CAPTCHA、限流、选择器漂移、模糊状态**永不**自动重试或绕过。

## 6. 真实提交（仅在 dry-run 通过后）

```bash
curl -X POST http://localhost:8000/api/v1/applications/<application_id>/platform-submissions/<run_id>/submit \
  -H "Content-Type: application/json"
```

### 验证清单

- [ ] 前置审批（approval boundary）已通过，否则返回 409。
- [ ] 外部幂等键已检查（重复提交返回已有终态结果，不再次调用适配器）。
- [ ] 适配器**至多点击一次**最终提交按钮（检查日志 `boss.adapter.submit`）。
- [ ] `submitted` 仅在观察到成功标记时返回；模糊状态返回 `unknown`。
- [ ] 结果中无 cookie / token / 原始 HTML。

## 7. 回滚 / 人工对账

如果 submit 返回 `unknown` 或 `platform_failure`：

1. **不要**自动重试。人工登录 BOSS Web 确认实际投递状态。
2. 在数据库中手动修正 application action 的 `external_result`（标注人工对账结论）。
3. 记录事件到 `check.jsonl`，说明模糊原因与人工结论。
4. 若是选择器漂移，更新 `backend/app/platforms/boss/selectors.py` 并补充测试。

---

## 8. 立即沟通（Immediate Communicate）试点

立即沟通流程与简历投递（submit）是两条独立的操作路径。投递填写表单并
点击「发送」提交简历；沟通则在职位卡片页点击「立即沟通」按钮，打开聊天
对话框，发送一条开场白消息。

### 8.1 前置条件

- **推荐**：油猴脚本已安装且已连接（模式 A）。油猴脚本桥接是 communicate 的
  首选路径——它在页面自身的 JS 上下文中执行，不会触发 BOSS 的 CDP 协议层自动
  化检测。
- **fallback only**：CDP 适配器（模式 B）作为油猴脚本不可用时的降级路径。
  CDP 模式有更严格的安全不变式（见下文），仅在用户确认愿意承担风控风险时启用。
- 已通过 JD 读取 + 匹配决策生成了 `boss_match_decision` artifact，且
  `decision == "communicate"`，`opening_message` 非空。
- 已调用 `POST /api/v1/boss/recommended-jobs/{job_id}/communicate/prepare`
  草拟了 `boss_immediate_communicate` action（状态 `approval_required`）。
- 用户已手动审批该 action（`POST /applications/{application_id}/actions/
  {action_id}/approve`）。

> **CDP fallback 安全不变式**（`RealBossAdapter.execute_communication`）：
>
> - **永不调用 `page.goto`**。CDP 模式只复用用户当前已打开的 Chrome 标签页，
>   通过 `connect_over_cdp` 连接，退出时只断开 CDP 客户端、**永不关闭**用户的
>   Chrome context。
> - **至多一次点击「立即沟通」+ 一次点击「发送」**（`assert click_count <= 1
>   and send_count <= 1`）。
> - **点击前**与**填充消息后**各校验一次页面 URL hash 与 `target_resource`
>   一致；不一致即返回 `unknown`（`page_binding_mismatch`），**不点击**。
> - 「继续沟通」可见而「立即沟通」不可见 → `duplicate`（对话已存在），不点击
>   不发送。两者都不可见 → `failed`（`selector_drift`），硬停止。
> - 结果分类与油猴脚本路径一致：`succeeded` / `duplicate` / `failed` /
>   `unknown`，优先级 success → duplicate → error → unknown。
> - 结果中**不包含**原始 URL、cookie、token、raw HTML 或消息原文。

### 8.2 执行立即沟通

```bash
curl -X POST \
  http://localhost:8000/api/v1/boss/recommended-jobs/<job_id>/communicate/<action_id>/execute \
  -H "Content-Type: application/json" \
  -H "X-User-Id: <user_id>" \
  -d '{"application_id": "<application_id>"}'
```

### 8.3 验证清单

- [ ] 前置审批（approval boundary）已通过，否则返回 409。
- [ ] 外部幂等键已检查（重复执行返回已有终态结果，不再次调用适配器）。
- [ ] 适配器**至多点击一次** `click_immediate_communicate`（「立即沟通」按钮）。
- [ ] 适配器**至多点击一次** `send_opening_message`（聊天对话框「发送」按钮）。
- [ ] 点击前页面 URL hash 与 `target_resource` 一致；填充消息后再次验证。
- [ ] **CDP fallback only**：适配器未调用 `page.goto`，未关闭用户 Chrome
      context（仅断开 CDP 客户端）。
- [ ] `succeeded` → `external_result_status == "submitted"`（confirmed send）。
- [ ] `duplicate` → `external_result_status == "duplicate"`（对话已存在，非失败）。
- [ ] `unknown` → `external_result_status == "unknown"`（硬停止，不自动重试）。
- [ ] `failed` → `external_result_status == "failed"` + failure envelope code。
- [ ] `read_communication_result` 在真实页面返回 `succeeded / duplicate_detected /
      platform_failure / unknown`（非总是 `unknown`——若总是 `unknown`，说明
      `:has-text()` 伪选择器未被 `querySelectorAllWithTextFilter` 正确处理）。
- [ ] 适配器执行完成后 `active_application_id` 已清空（channel cleared）。
- [ ] 结果中无 cookie / token / 原始 HTML / 原始消息内容。

### 8.4 沟通失败场景

| failure_code                    | 含义                              | 处理                               |
| ------------------------------- | --------------------------------- | ---------------------------------- |
| `bridge_not_connected`          | 油猴脚本未连接 / CDP 断开         | 重新连接适配器，确认页面在 zhipin  |
| `page_binding_mismatch`         | 页面 URL hash 不匹配（已导航离开）| 重新导航到目标职位页，重新执行     |
| `immediate_button_missing`      | 「立即沟通」按钮未找到            | 确认在职位详情页；可能选择器漂移   |
| `message_input_missing`         | 聊天输入框未找到                  | 「立即沟通」可能未成功打开对话框   |
| `send_result_unknown`           | 发送后页面状态无法分类            | 人工登录 BOSS 确认消息是否已发送   |
| `communication_failure`         | 通用沟通失败                      | 检查 `diagnostic_reference`        |
| `communication_duplicate_detected` | 对话已存在（非失败）           | 正常，无需重试                     |
| `communication_unknown_result`  | 模糊状态                          | 人工介入，**不**自动重试           |

### 8.5 幂等重放

对已产生终态结果的 communicate action 再次调用 execute：

- 返回 200（非 409），`external_result_status` 保持终态值不变。
- 消息包含 `幂等重放` 前缀。
- 适配器**不会被再次调用**（`communicate_calls` 长度不增加）。
- Timeline 记录 `boss_communicate_blocked` 事件，`reason=idempotency_replay`。

### 8.6 沟通回滚 / 人工对账

如果 execute 返回 `unknown`：

1. **不要**自动重试。人工登录 BOSS Web 查看聊天列表，确认消息是否已发送。
2. 在数据库中手动修正 `external_result`（标注人工对账结论）。
3. 记录事件到 `check.jsonl`，说明模糊原因与人工结论。
4. 若是选择器漂移（按钮/输入框/标记未找到），更新 `selectors.py` 中对应的
   `COMMUNICATION_*` 选择器并补充测试。

---

## 9. 禁用真实适配器

试点结束后，取消环境标志即可回退到 fake 适配器：

```bash
unset BOSS_USERSCRIPT_BRIDGE_ENABLED
unset BOSS_ADAPTER_ENABLED
unset BOSS_CDP_ENDPOINT
unset BOSS_SESSION_PROFILE_DIR
```

所有自动化测试默认在 fake 适配器下运行，不受影响。

## 安全不变量（任何时候都不得违反）

- 永不持久化 credentials / cookies / tokens / Playwright storage state / 原始 HTML / 原始 JD / 原始简历。
- prepare 永不点击最终提交按钮。
- submit 至多点击一次最终提交按钮。
- 立即沟通至多点击一次「立即沟通」按钮 + 一次「发送」按钮。
- CAPTCHA / 限流 / 选择器漂移 / 模糊状态 = 硬停止。
- 所有选择器和浏览器逻辑仅留在 `app/platforms/boss/` 内。
- `session_reference` / CDP endpoint / userscript 连接配置不走请求 / 队列 / DB，
  仅通过进程配置传入。
- CDP 模式下断开连接时**不关闭**用户的 Chrome context/page —— 只断开 DevTools 客户端。
- CDP 模式下**永不**调用 `page.goto` —— BOSS 检测 CDP 导航并销毁页面。用户手动导航。
- CDP endpoint 是进程配置，不走请求 / 队列 / DB。
- **油猴桥接模式下**：指令队列纯内存不持久化；回传结果只含脱敏值
  （visible/count/sha256(url)/截断title/stripped error）；永不发送 navigate
  指令（用户手动导航）；submit 的点击由后端 `UserscriptBossPage.click` 在
  submit 阶段精确触发一次；立即沟通的点击由后端在 execute 阶段精确触发
  一次 `click_immediate_communicate` + 一次 `send_opening_message`；
  无认证端点不携带 X-User-Id。
- **立即沟通安全约束**：Unknown result 永不触发自动重试；page hash 在点击前
  和填充后各验证一次（mismatch = `unknown` 硬停止）；channel 在每次 execute
  完成后清空（`active_application_id` 归 `None`）。
