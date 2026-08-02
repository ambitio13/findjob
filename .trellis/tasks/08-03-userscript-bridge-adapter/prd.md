# 油猴脚本桥接适配器替换 CDP/Playwright

## Goal

用 Tampermonkey 用户脚本桥接替换 RealBossAdapter 的 Playwright/CDP 后端，彻底绕过
BOSS 直聘在 CDP 协议层（`Page.navigate` / `Runtime.evaluate`）的反自动化检测。

## 背景

实测发现（详见 `docs/boss-anti-automation-findings.md`）：

- BOSS 在 CDP 协议层检测 `Page.navigate` → 页面被销毁为 `about:blank`
- BOSS 在 CDP 运行时层检测 `Runtime.evaluate` → 页面在首次 evaluate 后 ~0.5s 被关闭
- 检测是累积式的，一旦触发后续全部失败
- `navigator.webdriver` 隐藏等 JS 层反检测完全无效

**油猴脚本运行在页面自身的 JS 上下文中，没有 CDP 签名**，从根本上绕过 BOSS 检测。

## 架构决策

**后端发指令，油猴只执行**（用户明确选择）：

- 所有业务逻辑（分类器、安全不变量、审批边界）留在后端
- 油猴脚本极简，只执行后端下发的指令（fill/click/check/count/read）
- 油猴脚本不硬编码任何 BOSS 选择器——选择器从后端指令中获取
- 通信方式：HTTP 轮询（与现有 enqueue-and-poll 模式一致，无 WebSocket/SSE）

## Requirements

### R1: 内存指令队列

- 单进程内存队列，不进 Redis/DB，进程重启即清空
- 后端 push 指令，油猴脚本 HTTP 轮询 pull 指令
- 油猴脚本回传结果，后端等待对应结果（带超时）
- long-poll 模式：取指令最多等待 5s

### R2: UserscriptBossAdapter

- 实现 `PlatformAdapter` 协议（`prepare_submission` + `submit_prepared`）
- `UserscriptBossPage` 实现 `BossPage` 同接口，每个操作转为一条指令
- 复用现有 `classifiers.py` 和 `selectors.py`（不修改）
- prepare 永不发送 click 指令（`UserscriptBossPage.click` 在 prepare 阶段 raise）
- submit 恰好发送一次 click 指令

### R3: HTTP 桥接端点

- `GET /userscript-bridge/status` — 前端轮询连接状态
- `GET /userscript-bridge/next-instruction` — 油猴脚本取指令（long-poll 5s）
- `POST /userscript-bridge/result` — 油猴脚本回传结果
- `POST /userscript-bridge/heartbeat` — 油猴脚本心跳（5s 间隔）
- 无认证（油猴脚本不带 X-User-Id header）

### R4: 配置与注册

- 新增 `boss_userscript_bridge_enabled: bool = False`（进程配置，env 驱动）
- registry 三路选择：userscript > real(Playwright/CDP) > fake
- userscript 模式不加载 Playwright

### R5: Tampermonkey 用户脚本

- `@match https://www.zhipin.com/*`
- 轮询后端取指令，在页面内执行，回传结果
- `navigate` 指令限制为只读校验（不自动跳转，保持"不自动导航"不变量）
- 不存储任何 cookies/tokens

### R6: 前端桥接状态指示

- GuidedSubmitPanel 顶部显示油猴脚本连接状态（已连接/未连接）
- 3s 轮询 `GET /userscript-bridge/status`

## Acceptance Criteria

- [ ] `UserscriptBossAdapter` 实现 `PlatformAdapter` 协议，prepare/submit 全路径通过测试
- [ ] prepare 永不发送 click 指令（测试断言）
- [ ] submit 恰好发送一次 click 指令（测试断言）
- [ ] 指令队列纯内存，不持久化到 Redis/DB
- [ ] 回传结果只含脱敏值（visible/count/title/url_hash/error）
- [ ] navigate 指令限制为只读校验
- [ ] 4 个桥接端点通过测试
- [ ] registry 三路选择正确（测试覆盖）
- [ ] 现有 522 个测试全部通过（无回归）
- [ ] ruff check 通过
- [ ] 油猴脚本 `docs/boss-userscript.user.js` 可安装运行
- [ ] 前端显示桥接连接状态
- [ ] runbook `docs/manual-boss-pilot.md` 更新油猴模式步骤

## 安全不变量（全部保留）

1. 一次调用只处理一个 application_id，无批量/自主路径
2. prepare 永不点击最终提交按钮
3. submit 必须先过外部幂等键 + `assert_action_approved`
4. 永不持久化 credentials/cookies/tokens/原始 HTML/原始 JD/原始简历
5. CAPTCHA、限流、选择器漂移、模糊页面状态 = 硬停止
6. 所有选择器和浏览器逻辑仅留在 `app/platforms/boss/` 内
7. session_reference / bridge 连接状态不走请求/队列/DB
8. 不自动导航（navigate 指令限制为只读校验）
9. 跨用户访问返回 404
10. 审批边界不变（service 层不变）
