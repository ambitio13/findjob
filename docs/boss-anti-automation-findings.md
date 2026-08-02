# BOSS 直聘反自动化检测实测报告

> 记录日期：2026-08-02
> 测试环境：macOS (darwin 25.5.0 arm64)，真实 Chrome，Playwright (chromium)
> 测试目标：确认 `RealBossAdapter` 的 CDP 连接方式能否在真实 BOSS 直聘上工作

## 结论（TL;DR）

**可行路径**：通过 CDP 连接真实 Chrome，但**永不调用 `page.goto`**。用户手动在
Chrome 窗口中导航到目标页面，适配器通过 CDP 只做读取和点击操作。

**不可行路径**（已被实测排除）：

| # | 方式 | 结果 |
|---|------|------|
| 1 | Playwright `launch_persistent_context`（自带 Chromium） | `about:blank`，风控拦截 |
| 2 | `launch_persistent_context(channel="chrome")` | 首次加载，第二次 `about:blank`（被标记后拦截） |
| 3 | CDP `connect_over_cdp` + `page.goto` 导航到 `/web/geek/*` | 页面被销毁为 `about:blank` |
| 4 | CDP + `Runtime.evaluate` 调用 | 页面在首次 evaluate 后 ~0.5s 被关闭 |
| 5 | `add_init_script` 反检测（隐藏 `navigator.webdriver` 等） | 无效 |

## 1. 测试方法

系统性逐一验证每种连接/导航方式，观察 BOSS 前端 JS 的反应。

### 1.1 Playwright 自带浏览器

```python
browser = await pw.chromium.launch_persistent_context(
    user_data_dir=profile_dir,
    headless=False,
    args=["--disable-blink-features=AutomationControlled"],
)
page = await browser.new_page()
await page.goto("https://www.zhipin.com/web/geek/job-recommend")
```

**结果**：页面返回 `about:blank`，或在登录页和职位页之间反复重定向。
`--disable-blink-features=AutomationControlled` 无效。

### 1.2 Playwright 启动真实 Chrome（channel="chrome"）

```python
browser = await pw.chromium.launch_persistent_context(
    user_data_dir=profile_dir,
    channel="chrome",
    headless=False,
)
```

**结果**：第一次导航能加载页面，第二次导航返回 `about:blank`。BOSS 在第一次访问
后将浏览器标记，后续全部拦截。

### 1.3 CDP 连接 + page.goto

```bash
# 启动真实 Chrome
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.boss-debug-chrome"
```

```python
browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
ctx = browser.contexts[0]
page = ctx.pages[0]
await page.goto("https://www.zhipin.com/web/geek/job-recommend")
```

**结果**：页面立即被销毁为 `about:blank`。即使浏览器指纹是 100% 真实 Chrome，
BOSS 的前端 JS 能检测到 CDP 的 `Page.navigate` 命令并主动销毁页面。

### 1.4 CDP 连接 + Runtime.evaluate

```python
# 页面已在 Chrome 中手动打开，不做 goto
page = browser.contexts[0].pages[0]
await page.evaluate("document.title")  # 第一次 evaluate
```

**结果**：页面在首次 evaluate 后约 0.5 秒被 BOSS 关闭。

报错信息：
```
TargetClosedError: Page.evaluate: Target page, context or browser has been closed
```

### 1.5 add_init_script 反检测

```python
await ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    // 删除 CDP 指纹、伪造 plugins/languages 等
""")
```

**结果**：偶尔有效（根域名 → recommend 一次成功），但后续尝试全部失败。
BOSS 的检测**不依赖** `navigator.webdriver`，而是基于 CDP 协议层面的行为特征。

### 1.6 CDP 原生命令 Page.navigate

```python
client = await page.context.new_cdp_session(page)
await client.send("Page.navigate", {"url": "https://www.zhipin.com/..."})
```

**结果**：偶尔绕过 `page.goto` 检测，但不可靠——检测是累积的，一旦在任何页面触发，
后续全部失败，直到重启 Chrome。

### 1.7 Chrome 启动参数 URL（对照实验）

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.boss-debug-chrome" \
  "https://www.zhipin.com/web/geek/job-recommend"
```

**结果**：页面正常加载，不触发检测。

**原因**：Chrome 启动时还没有 CDP 客户端连接，导航发生在 CDP 连接建立之前，
所以 BOSS 的前端 JS 检测不到 CDP 行为。

## 2. BOSS 检测机制总结

BOSS 直聘的反自动化检测是**多层级、累积式**的：

1. **浏览器指纹层**：识别 Playwright/Selenium 启动的浏览器（即使加了反检测参数）
2. **CDP 协议层**：检测 CDP 的 `Page.navigate` 命令 → 销毁页面
3. **CDP 运行时层**：检测 CDP 的 `Runtime.evaluate` 调用 → 关闭页面
4. **累积标记层**：一旦在任何一个页面触发检测，后续所有导航都失败，必须重启 Chrome

关键洞察：BOSS **不依赖** `navigator.webdriver`，而是基于 CDP 协议层面的行为特征
（导航命令、evaluate 调用）。这意味着传统的 JS 层反检测脚本完全无效。

## 3. 可行方案：CDP + 手动导航

基于以上发现，`RealBossAdapter` 的 CDP 模式采用以下策略：

```text
用户操作：
  1. 关闭所有 Chrome 窗口
  2. 用 --remote-debugging-port=9222 启动真实 Chrome
  3. 在 Chrome 中手动登录 BOSS
  4. 手动导航到目标职位/HR 会话页面

适配器操作：
  1. connect_over_cdp 连接到 Chrome
  2. 复用 browser.contexts[0].pages[0]（用户已打开的页面）
  3. 不调用 page.goto
  4. 通过 CDP 读取 DOM 和点击元素（尽量减少 evaluate 调用）
  5. 断开连接时不关闭用户的 Chrome
```

### 3.1 已知限制

- **evaluate 调用需最小化**：BOSS 会在多次 evaluate 后关闭页面。适配器的
  `classify_page`、`fill`、`click` 操作底层都依赖 evaluate，必须尽量精简。
- **页面可能被关闭**：如果 BOSS 关闭了页面，适配器返回 `unknown`（硬停止），
  用户需手动重新打开页面后重试。
- **不支持自动导航**：适配器无法自动从一个职位跳到另一个职位。每次操作前
  用户需手动导航到目标页面。

### 3.2 代码对应

| 行为 | 代码位置 | 说明 |
|------|----------|------|
| CDP 连接，不调用 goto | `runtime.py` `_OpenContext.__aenter__` | CDP 分支只复用已有页面 |
| 断开连接不关 Chrome | `runtime.py` `_OpenContext._close_cdp` | 只 `browser.close()`（断开 CDP） |
| 复用已有页面 | `runtime.py` `_OpenContext._open_cdp` | `contexts[0].pages[0]` |

## 4. 后续优化方向（记录，不在本任务范围内）

1. **最小化 evaluate 调用**：考虑使用 CDP DOM API（`DOM.getDocument`、
   `DOM.querySelector`）替代部分 `Runtime.evaluate`，减少被检测概率。
2. **操作间延迟**：在读取/点击操作之间加入随机延迟，模拟人类行为节奏。
3. **页面存活检测**：每次操作前检查页面是否仍存活，被关闭时立即返回 `unknown`。
4. **多标签页策略**：用户在 Chrome 中预开多个目标页面，适配器轮询使用，
   延长可用窗口。
5. **替代协议探索**：调研 Chrome 扩展方式（`chrome.tabs` API）或
   AppleScript/UI 自动化方式，完全绕过 CDP 协议层检测。

## 5. 安全不变量（本方案不影响）

以下安全不变量在 CDP 模式下**完全保留**：

- 一次调用只处理一个 `application_id`，无批量/自主路径
- prepare 阶段可读取/填充，但永不点击最终提交按钮
- submit 阶段必须先过外部幂等键 + `assert_action_approved` 才调用适配器
- 永不持久化 credentials/cookies/tokens/Playwright storage state/原始 HTML/原始 JD/原始简历
- CAPTCHA、限流、选择器漂移、模糊页面状态 = 硬停止
- 所有选择器和浏览器逻辑仅留在 `app/platforms/boss/` 内
- Playwright 仍为懒加载，默认测试/开发无需安装
- `session_reference` / CDP endpoint 不走请求/队列/DB，仅进程配置
- CDP 模式下断开连接时不关闭用户的 Chrome context/page
- CDP 模式下永不调用 `page.goto`
- `~/.boss-debug-chrome` 目录等价于登录态，不提交 git，不共享
