# BOSS 推荐列表发现与采集流水线 — 计划评估简报

> 用途：供第三方评估 `08-13-boss-recommended-discovery-pipeline` 任务的 v2 计划。
> 日期：2026-08-13。作者：coldnight（含两轮外部审核意见的取舍记录）。
> 关联文件（同目录）：`prd.md`（需求）、`design.md`（v2 设计）、`implement.md`
> （执行计划）、`probe-evidence.md`（原始取证结论）、`task.json`。

> 2026-08-13 追加修订：后续代码契约审核指出 `is_new_job=False` 不能直接
> `skipped(already_persisted)`，因为现有 inspect 语义仍会为当前简历创建 /
> 复用 `ApplicationRecord`。当前 PRD/design/implement 已修正为：仅当当前
> job+resume 已有可复用 communicate action 或终态 result 时才
> `skipped` + `skip_reason=already_persisted`；单纯已有 JobPosting 仍继续
> match/prepare。另补充了 new-tab 不可可靠检测、resume 重扫恢复、跨路径
> 重复 JobPosting 的 v0.1 tradeoff、AgentRun.status 映射、以及 channel
> sentinel 锁生命周期。下文取舍表保留原评审脉络，以上述当前三件套为准。

---

## 1. 任务目标与 v0.1 边界

在现有 BOSS batch-loop（对已入库 `job_ids` 串行 match → prepare，停在审批前）
之前补一个"页面发现层"：用户打开 BOSS 推荐列表页后，系统扫描可见职位卡片，
逐个切换读取 JD，入库去重，复用现有匹配与 prepare-only 审批队列。

硬边界（不可逾越）：

- 不自动 approve / 不自动发送 / 不自动投递；`auto_execute` 由 dry-run log
  gate 在 API 层拒绝（沿用 evolution-contracts §14）。
- 自动路径不得携带 `human_review`（§12）。
- 不绕过验证码 / 限流 / 登录态检测；不走 CDP / Playwright 自动化路线
  （`docs/boss-anti-automation-findings.md` 已实测该路线被 Boss 风控拦截）。

## 2. 验证方法：如何获得这些事实

### 2.1 被排除的验证路线（及排除证据）

| 路线 | 结果 | 证据 |
|---|---|---|
| 独立浏览器实例（Browser 子代理 / chrome-devtools MCP） | 无登录态，跳登录页；且实例被风控标记，Boss 页面加载后被销毁为 `about:blank` | 本会话实测：导航 `/web/geek/*` 页面销毁；首页首次 evaluate 后 ~0.5s 销毁。精确命中 `docs/boss-anti-automation-findings.md` 案例 3/4 |
| ComputerUse 接管日常 Chrome | 用户取消，未执行 | — |
| 在风控实例里扫码登录 | 主动放弃 | 被标记实例登录后仍会被销毁，且有账号风控风险 |

结论：**真机取证只能走 userscript 路线**——脚本运行在用户日常已登录
Chrome 的正常页面上下文里，无 CDP/自动化特征，与项目既有 bridge userscript
同一信任模型。

### 2.2 采用的取证工具与纪律

- 工具：`docs/boss-discovery-probe.user.js`（只读诊断油猴脚本）。
- 纪律：不点击、不导航、不上传、不发任何网络请求；报告只渲染在本页浮层，
  由用户人工复制回传。链接/按钮只统计 `target`、class、文案，不触发。
- 证据回传：3 轮探针 JSON（首页 / 列表页 / 详情页 / pane 隔离）+ 用户两次
  人工交互观察 + 1 张页面截图。
- 可信度说明：探针输出为 DOM 计数与结构，非主观描述；关键交互行为
  （点击卡片本体 = pane 原地切换；点击标题链接 = 新窗口整页加载）由用户
  人工操作观察，并与 DOM 证据（`active` class 随点击迁移）互证。

### 2.3 代码侧核对

设计引用的所有复用边界均逐条对照真实代码确认存在且语义一致：

- `inspect_current_job` / `upsert_job_from_browser_jd`（dedup 键
  `platform="boss" + external_id=page_url_hash`，缺失抛 ValueError）
- `run_boss_match_decision`、`prepare_communicate_action`、
  `boss_dry_run_gate.assert_auto_execute_allowed`
- batch-loop 的 `AgentRun.result["items"]` 进度模型、同步执行、同步 pause
  语义、跨用户 404、`set_active_application` 单锁冲突抛 RuntimeError
- `RESULT_TIMEOUT_S = 90.0`（同步执行超时预算的依据）

## 3. 探测到的事实清单

### 3.1 页面形态：master-detail，零导航

- `/web/geek/jobs`（推荐 tab）= 左卡片列表 + 右内嵌详情 pane。
- 点击**卡片本体**（`div.job-card-wrap`）：pane 原地切换，不转圈、不开新
  窗口，URL 全程不变。互证：点击第二张卡后首卡 `active` class 消失。
- 点击**标题链接** `a.job-name`：Boss JS 打开新窗口并整页加载
  `/job_detail/*.html`（用户实测）。此为陷阱路径。
- 城市首页（`/foshan/`）是另一套 DOM（`a.job-info`，126/127
  `target="_blank"`），与 geek 列表页不同构。

### 3.2 卡片列表 DOM（计数来自探针）

- 卡片根 `div.job-card-wrap`（`.job-card-wrapper`/`.job-card-body` 不存在）。
- 标题链接 `a.job-name` ×16（16/17 无 target）；父链
  `a.job-name < div.job-title clearfix < div.job-info`。
- 字段：`.job-name` 标题、`.job-salary` 薪资（不是 `.salary`）、
  `[class*=company]` 公司、`[class*=area]` 地点（15，个别缺失）、
  `.tag-list li` 标签（72）。
- **无沟通/已投递徽标**（`[class*=communicate]`=0，页面无"继续沟通/已沟通"
  文本）。

### 3.3 内嵌 pane DOM

容器 `div.job-detail-container`（隔离规则：含"职位描述"文本且不含
`.job-card-wrap` 的最大容器）。

- 标题+薪资：`.job-detail-header .job-header-info`（readiness 比对源）。
- **`h3.title` 是"职位描述"小节标题，不是职位标题**（探针陷阱，已识别）。
- 正文 `p.desc`（~500 字）；标签 `ul.job-label-list li`；地点 `.job-address`。
- pane 内**无公司字段**（`[class*=company]`=0）→ 公司取自卡片侧。
- `.job-boss-info` = 招聘者信息（含人名）→ 隐私禁采。
- 按钮：`.op-btn-chat`（立即沟通）、`.op-btn-like`（收藏）、
  `a.more-job-btn`（查看更多信息，会导航）→ 一律禁点。
  其中 `.op-btn-chat` 经用户人工实测：点击后当前 tab 导航到
  `/web/geek/chat`，列表页上下文立即销毁（chat 页无卡片/无 pane）——
  既是外部副作用入口又是同 tab 导航陷阱；运行中用户手动误点时，下一个
  op 以 `page_mismatch` 安全硬停止。
- 同级噪音：`.c-job-tools`、`.c-hot-link`、`.c-breadcrumb` → 读取排除。

### 3.4 风控事实

CDP/自动化浏览器实例会被 Boss 前端 JS 检测并销毁页面（与 2026-08-02 实测
报告一致）。任何实现不得引入 CDP 特征；userscript 路线不受影响。

## 4. 审核意见取舍表

两轮外部审核 + 一轮自审的意见，逐条给出验证方式与结论。

| # | 意见 | 验证方式 | 结论 |
|---|---|---|---|
| P0 | 卡片可能 `target="_blank"`，若真则 v0.1 空转，应设 go/no-go 门禁 | 探针 `anchor_target_stats` + 用户人工点击观察 | **原担忧不成立**：卡片本体点击零导航。门禁以取证方式完成；保留 `new_tab_not_supported` 防御分支 |
| P1-1 | 同页 pane 下 `page_url_hash` 全相同，upsert 互相覆盖 | 代码核对（dedup 键确为 page_url_hash 且缺失抛错）+ 探针确认 URL 不变 | **采纳**：discovery 以 `job_key`（href 路径 sha256）作 `external_id`；upsert 增加 `external_id_override` 扩展点，inspect 路径不变 |
| P1-2 | scan 期检测已沟通/已入库缺 key 映射 | 探针确认列表页无任何沟通徽标 | **修正采纳**：scan 期检测删除；且 `is_new_job=False` 单独不足以跳过（inspect 仍会为当前简历关联/创建 ApplicationRecord），仅当当前 job+当前简历已有待审批或终态 communicate action 时 `skipped(skip_reason=already_persisted, failure_code=null)`，否则继续 match→prepare |
| P1-3 | 同步执行 vs 前端轮询自相矛盾；limit=10 最坏超代理超时 | 代码核对（batch-loop 同步、`RESULT_TIMEOUT_S=90`） | **采纳并定案**：v0.1 同步 + limit 默认 3（上限 10）；前端实时进度与可用暂停降级为未来异步版本；PRD/AC 同步改口径 |
| P1-4 | bridge 锁未定义；batch-loop 与 discovery 可能交错 | 代码核对（单锁 RuntimeError；`_find_active_run` 按 workflow_type） | **采纳**：run 级互斥（409 `active_conflict`）+ channel sentinel 锁 `discovery:<run_id>` |
| P2-5 | resume 中间态恢复语义缺失 | 零导航方案下 scan 缓存全程存活，中间态仅存在于崩溃场景 | 随方案消解；pause/resume 仅作崩溃恢复，中间态 item resume 时重扫或标 `stopped`（design 已写明） |
| P2-6 | URL hash 绑定链 | 零导航，URL 全程不变 | 消解 |
| P2-7 | `AgentRun.result` 明文存 title/company | 确认该列为明文 JSON | **接受并记录**：短脱敏字段可接受，design 隐私节显式记录权衡；JD/简历文本绝不入此列 |
| P2-8 | AC6 与 localStorage optional 矛盾 | — | **采纳**：AC6 改为 run id / resume 选择器恢复，实时进度非 v0.1 承诺 |
| 自审-1 | selector 需 probe-first（契约 §13） | 取证已完成 | 采纳：`boss_recommended_list_v1` fallback chain 以证据定稿 |
| 自审-2 | 新增返回页面文本的 op 需隐私评审（契约 §4） | — | 采纳：review gates 增加隐私评审条目；scan/read 不进 prod probe 白名单 |
| 自审-3 | item 初始态命名 `discovered` vs `pending` 不一致 | — | 统一为 `pending` |

## 5. 计划修改清单（v1 → v2）

1. **流水线形态**：`open(导航/新tab) + go_back + 重扫` → **零导航**
   `scan → 点卡片本体 → wait pane 标题匹配 → read pane → upsert → match → prepare`。
   理由：证据 §3.1。收益：scan 缓存与 bridge 页绑定全程存活，删除
   go_back/重扫/多 tab 编排三类复杂度。
2. **op 契约**：`open_job_by_key` 改为只点卡片根节点并校验 URL 未变
   （变化即 `unexpected_navigation` 硬停止）；`wait_job_detail_ready` 以
   `.job-header-info` 文本包含被点卡片标题为就绪条件；`read_jd` 新增 pane
   profile `boss_recommended_pane_v1`（白名单排除 `.job-boss-info` 等）。
3. **去重键**：`page_url_hash` → `job_key`（`external_id_override`）。
4. **item 状态机**：`discovered` 退役为 `pending`；删除
   `already_communicated`（无徽标证据）。
4b. **跳过语义细化**（R3.1）：`already_persisted` 不再是“职位已入库就跳
   过”，而是“当前职位+当前简历已有可复用的待审批/终态 communicate action
   才跳过”；单纯 `JobPosting` 已存在但当前简历无 action 时继续
   match→prepare。理由：`inspect_current_job` 语义会继续为当前
   `resume_version_id` 查找或创建 `ApplicationRecord`，直接跳过会漏掉换
   简历后应重新匹配/草拟的场景。
5. **执行形态**：limit 默认 10 → 3；前端实时进度/暂停降级；pause 仅崩溃恢复。
6. **并发与锁**：新增 run 级互斥 409 与 channel sentinel 锁。
7. **failure matrix**：新增 `unexpected_navigation`、`pane_not_found`、
   `detail_not_ready`（pane 标题超时）、`already_persisted`；保留连续 3
   failed 硬停与 captcha/rate-limit 硬停。
8. **Out of Scope**：新增禁点（`a.job-name`/`a.more-job-btn`/`.op-btn-chat`/
   `.op-btn-like`）、禁采（`.job-boss-info`）、城市首页、实时进度。

## 6. 剩余风险与未验证假设（请评估者重点挑战）

1. **pane 异步渲染时延**：点击卡片后 pane 内容异步刷新的耗时未量化，
   `wait_job_detail_ready` 超时阈值需真机 pilot 校准（初值将有界常量）。
2. **合成点击 vs 真实点击**：`open_job_by_key` 若用合成 click 派发，Boss
   前端是否同等响应（pane 切换）未验证；pilot 第一步即验证，失败则回退
   真实 `el.click()`（仍根节点）。
3. **推荐流刷新/重排**：BOSS 推荐列表可能在后台刷新后重排；v0.1 只处理
   一次 scan 的可见集合，影响有限，但 `candidate_not_found` 需可解释。
4. **selector drift**：Boss 改版随时可能发生。防御：probe-first 证据 +
   fallback chain + `pane_not_found`/`unexpected_navigation` 硬停 + 测试页
   fake DOM 回归。
5. **同步请求时长**：limit 3 × （bridge op ~1s + LLM match 数秒）通常 < 1
   分钟，可接受；但需在 pilot 中观测 P95，作为未来异步化的决策输入。
6. **样本单一性**：证据来自单一账号/城市/时点（含校招卡片）。不同城市、
   社招/校招、登录态时长下的 DOM 差异未知 → rollout 规定先 limit 3 真机
   小样本验证后再放量。
7. **明文 JSON 列**：`AgentRun.result` 存短脱敏摘要为接受项，若评估者要求
   更高保密级别，可选方案是摘要也哈希化（代价：运营可见性下降）。

## 7. 验证与验收计划（如何证明实现正确）

- **后端测试**：bridge sanitize 白名单、scan empty/success、per-item
  prepare-only、`already_persisted` 跳过且重置连败计数、needs_review/skip、
  `unexpected_navigation`/`pane_not_found` 硬停、3 连败硬停、跨用户 404、
  互斥 409、auto_execute gate 拒绝、`external_id_override` 不回归 inspect。
- **userscript 测试页**（`docs/boss-userscript-tests.html`）：fake 列表 +
  fake pane；断言禁点（不触发 `a.job-name`/`.op-btn-chat` 等）与禁采
  （结果不含 `.job-boss-info` 内容）。
- **回归**：单职位 inspect、batch-loop、human-review override、审批 execute
  全量测试不回归；`ruff`/`pytest`/前端 lint/type/build/e2e smoke。
- **真机 pilot 清单**：limit 3、prepare-only；运行全程确认零导航、无新窗口；
  `communicate` 仅产生 `approval_required`；无消息发送、无简历投递；结果
  记入 runbook。
- **回滚**：摘除 discovery 路由与面板入口；既有 read_jd(detail profile)、
  inspect、batch-loop、execute 独立可用。

## 8. 附录：文件索引

- 需求：`prd.md`；设计：`design.md`；执行：`implement.md`
- 取证：`probe-evidence.md`；探针工具：`docs/boss-discovery-probe.user.js`
- 上下文清单：`implement.jsonl` / `check.jsonl`
- 约束源：`.trellis/spec/backend/evolution-contracts.md`（§4 隐私、§11 路由、
  §12 人审、§13 probe-first、§14 batch-loop）
- 风控实测：`docs/boss-anti-automation-findings.md`
