# JS 测试页（J1-J12）

## Goal

创建 `docs/boss-userscript-tests.html`，一个独立 HTML 测试页，内嵌
`querySelectorAllWithTextFilter()` 源码 + 构造的 DOM + 断言，验证油猴脚本中的
`:has-text()` 伪选择器处理逻辑。开发者用浏览器打开即可运行，零依赖。

## Confirmed Facts

- `querySelectorAllWithTextFilter()` 是油猴脚本中的核心函数，负责处理 Playwright-only
  的 `:has-text()` 伪选择器（剥离后运行 `querySelectorAll`，再按 `textContent.includes()`
  过滤）。
- 当前 JS 层零测试覆盖。所有后端测试通过 `FakeUserscriptChannel` 注入预定义结果，不经过
  真实 DOM 查询逻辑。
- 项目无根级 `package.json`，无 JS 测试框架。设计文档明确说"Browser script logic is hard
  to unit test → backend services/adapters are testable"。
- 方案 A（独立 HTML 测试页）是推荐方案：零依赖、无需安装、可视化、直接在真实浏览器 DOM
  上运行。

## Requirements

### R1. 测试页文件

创建 `docs/boss-userscript-tests.html`，包含：
- 内嵌 `querySelectorAllWithTextFilter()` 函数源码（从 `boss-userscript.user.js` 复制）
- 构造的模拟 BOSS DOM 元素
- 断言逻辑（pass/fail 可视化显示）
- 打开浏览器即可运行，无需安装任何依赖

### R2. 12 个测试用例（J1-J12）

| # | 用例 | 输入 | 预期结果 |
|---|------|------|---------|
| J1 | 单个 `:has-text()` 匹配 | `.btn:has-text('继续沟通')` + DOM 含 `<button class="btn">继续沟通</button>` | 返回 1 个元素 |
| J2 | `:has-text()` 不匹配 | 同上但按钮文本是"投递简历" | 返回 0 个元素 |
| J3 | 逗号分隔混合列表 | `.btn:has-text('已发送'), .status` + DOM 各有一个 | 返回 2 个元素 |
| J4 | 无 `:has-text()` 纯 CSS 透传 | `.error-tip, .upload-error` + DOM 各有一个 | 返回 2 个元素 |
| J5 | 清理后 CSS 为空 | `:has-text('x')`（无 CSS 前缀） | 返回空数组（不抛异常） |
| J6 | 多个 `:has-text()` AND 语义 | `.btn:has-text('a'):has-text('b')` + DOM 含 `ab` 和 `a` | 仅匹配含 `ab` 的元素 |
| J7 | 真实 SUCCESS_MARKER | 完整 `COMMUNICATION_SUCCESS_MARKER` + 模拟 BOSS 聊天 DOM | 成功匹配 |
| J8 | 真实 DUPLICATE_MARKER | 完整 `COMMUNICATION_DUPLICATE_MARKER` + 模拟"继续沟通"按钮 | 成功匹配 |
| J9 | B1 bug 验证 | userscript 的 `.error-message` vs selectors.py 的 `.error-tip` — 同一 DOM | 演示不一致（已修复） |
| J10 | B2 bug 验证 | 同时存在 success + duplicate 标记 | duplicate 优先 |
| J11 | 空输入 | `""` | 返回空数组 |
| J12 | 双引号变体 | `:has-text("已发送")` | 正常匹配 |

### R3. 结果展示

- 每个用例显示 pass/fail 状态（绿色/红色）
- 失败时显示期望值 vs 实际值
- 页面顶部显示总通过数 / 总数

## Acceptance Criteria

- [ ] `docs/boss-userscript-tests.html` 文件已创建
- [ ] J1-J12 全部通过
- [ ] 浏览器打开即可运行，无需安装依赖
- [ ] 失败用例有清晰的期望值 vs 实际值对比

## Notes

- 详细用例设计见 `docs/boss-communicate-testing-plan.md` 第 2.2 节。
- `querySelectorAllWithTextFilter()` 源码在 `docs/boss-userscript.user.js` 中。
- 非自动化测试，不纳入 CI。
