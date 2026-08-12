# BOSS 推荐列表发现与采集流水线

## Goal

补齐 BOSS 推荐列表的页面发现层：用户打开 BOSS 推荐列表页后，系统从当前可见职位
卡片中发现候选，逐个打开详情读取 JD，入库去重，并复用现有匹配与
prepare-only 审批队列。

第一版定位为确定性自动化流水线 + LLM 节点，不做自由 agent 规划，不自动审批、
不自动发送、不自动投递。

## Background

现有 `08-03-boss-recommended-list-batch-loop` 已实现批量处理已入库 `job_ids`：
串行 match → prepare，并停在审批前。但它不负责从 BOSS 推荐列表页面发现职位，
因此用户仍需逐个打开职位并点击“读取当前职位”。

现有能力可复用：

- userscript bridge 已支持 `read_jd`、page_id 绑定、URL hash 校验、心跳和脱敏结果。
- `inspect_current_job` 已能把当前页 JD upsert 为 `JobPosting` / `ApplicationRecord`。
- `run_boss_match_decision` 已能产出 `communicate` / `needs_review` / `skip`。
- `prepare_communicate_action` 已能把 `communicate` 决策草拟为 `approval_required` action。
- `human_review` 覆写路径已补齐，但只能由人工显式点击触发，自动路径不得携带。

## Requirements

### R1. 推荐列表扫描

- selector profile 必须 probe-first：真机取证已完成（probe-evidence.md），
  `boss_recommended_list_v1` fallback chain 以该证据为准：卡片根
  `div.job-card-wrap`、标题 `.job-name`、薪资 `.job-salary`、公司
  `[class*=company]`、地点 `[class*=area]`、标签 `.tag-list li`。
- userscript 新增只读 op `scan_visible_jobs`，只扫描当前 BOSS 推荐列表页
  （`/web/geek/jobs` master-detail 布局）可见职位卡片。
- 返回每个候选的脱敏摘要：`job_key`、`rank`、`title`、`company`、`salary`、
  `location`、`tags`、`candidate_hash`。`job_key` 由卡片标题链接 href 路径
  在 userscript 本地派生（sha256），跨扫描稳定；raw href 不返回、不落库。
- 页面不存在沟通/已投递徽标，scan 不返回 communicated 类标志；重复检测只
  发生在入库后（见 R3.1）。
- 不返回 raw HTML、cookie、token、联系人姓名、聊天内容或完整原始 URL。
- 第一版只处理当前可见职位，不做无限滚动和全站抓取；城市首页等其他 DOM
  形态不支持。

### R2. 受限切换职位（零导航）

- 真机取证（probe-evidence.md）：点击卡片本体会让右侧内嵌详情 pane 原地
  切换，不导航、不开新窗口；点击标题链接 `a.job-name` 才会开新窗口，属
  于陷阱路径。
- userscript 新增动作 op `open_job_by_key`，只允许点击最近一次扫描缓存中
  某个 `job_key` 对应的**卡片根节点**；绝不点击 `a.job-name`、
  `a.more-job-btn`、`.op-btn-chat`、`.op-btn-like` 或任何后代链接/按钮。
- 后端不得下发任意 selector 点击；userscript 也不得执行未在扫描缓存中的
  key。
- 点击后校验 URL 未变化；若发生导航，返回 `unexpected_navigation` 并触发
  硬停止。
- `wait_job_detail_ready` 以 pane 头部标题包含被点卡片标题为就绪条件，超
  时有界。
- v0.1 只支持同页详情 pane。当前 tab 的 userscript 不能可靠感知新标签页
  是否被打开；因此设计通过禁点清单规避已知新窗口路径，并由真机 pilot
  确认运行期间不产生新窗口。`new_tab_not_supported` 仅作为防御/人工对账
  语义，不作为可保证的运行时检测能力。

### R3. Discovery workflow

- 后端新增 BOSS discovery workflow，串行执行（全程零导航）：
  `scan_visible_jobs -> open_job_by_key -> wait_job_detail_ready -> read_jd(pane) -> upsert -> match -> prepare`。
- 每次最多处理 `limit` 个候选，默认 3，后端强制上限 10；默认值与真实页面
  小样本 rollout 保持一致。
- 每个 item 都有独立终态：`pending`、`opening`、`reading`、`persisted`、
  `matching`、`prepared`、`needs_review`、`skipped`、`failed`、`stopped`。
- 列表页 URL 全程不变，`page_url_hash` 不能做去重键；discovery 入库以
  `job_key` 作为 `external_id`。单职位 inspect 路径保持原有
  `page_url_hash` 语义不变。
- 批量过程继续复用 `AgentRun.result` 记录进度，不新增表，除非实现时发现
  `AgentRun.result` 无法满足恢复/审计。

### R3.1 已存在候选处理

- 真机取证：列表页不存在沟通/已投递徽标，scan 期无法检测已沟通；重复检测
  只发生在 upsert 后。
- upsert 发现职位已入库（`is_new_job=False`）时，不能直接跳过：现有
  `inspect_current_job` 语义会继续为当前 `resume_version_id` 查找或创建
  `ApplicationRecord`。因此 discovery 必须继续判断当前职位 + 当前简历是否
  已经有可复用处理结果。
- 仅当当前职位 + 当前简历已有非终态待审批 communicate action，或已有终态外部
  action 结果时，该 item 标记为 `skipped`，`skip_reason="already_persisted"`，
  不计入连续失败。
- 如果只是 `JobPosting` 已存在，但当前简历没有 application / action，仍继续
  match -> prepare。
- 业务跳过结果不写 `failure_code`，不触发 hard stop。

### R4. prepare-only 审批边界

- discovery 第一版只生成待审批 action，不自动 approve、不自动 execute。
- `communicate` 决策可自动 prepare 为 `approval_required`。
- `needs_review` 和 `skip` 只记录结果并停给人工处理；自动流程不得发送
  `human_review`。
- dry-run gate 未通过时，任何 `auto_execute` 入口仍必须由后端拒绝。

### R5. 前端巡航面板

- 前端新增“推荐列表巡航”入口，显示 bridge 连接状态、当前页面状态、limit、
  开始按钮、结果表和结果分类。
- v0.1 为同步执行：POST 返回终态结果表；刷新后可通过 run id / resume 选择
  器恢复查看。实时逐条进度与暂停按钮留给未来异步版本（v0.1 的 pause 仅
  用于崩溃恢复）。
- 崩溃恢复 / resume 必须先重新执行 `scan_visible_jobs` 以重建 userscript
  内存缓存；如果无法重扫当前列表，非终态 item 标记为 `stopped`，不得让
  失效缓存造成连续 `candidate_not_found` 硬停。
- `approval_required` 项提供跳转到现有审批/执行区域的入口。
- `needs_review` / `skip` 项提示可进入人工覆写路径，但不自动展开或提交。

### R6. 硬停止与可解释失败

- 以下情况硬停止或 item failed：bridge 未连接、wrong tab、page mismatch、
  点击后发生意外导航（`unexpected_navigation`）、pane 容器缺失
  （`pane_not_found`）、pane 标题超时不匹配、captcha、rate limit、
  selector drift、JD too sparse、连续 3 个 failed、
  用户切换页面导致 URL hash 不匹配。
- 错页与空列表必须区分：不在 `/web/geek/jobs` master-detail 列表页或缺少
  `div.job-card-wrap` 列表容器时返回 `not_recommended_list_page`；容器存在
  但无可见候选时才返回空结果 / `no_visible_jobs` message。
- `skip` / `needs_review` 是正常业务结果，不计入连续失败。
- 每个失败 item 必须有稳定 `failure_code`、可展示 message 和脱敏 diagnostic。

## Acceptance Criteria

- [ ] AC1: 用户打开 BOSS 推荐列表页，点击“开始巡航”后，系统能扫描当前可见职位并返回候选列表。
- [ ] AC2: 系统能串行切换候选卡片、读取同页 pane JD、以 `job_key` 入库去重，并返回 job/application id；仅当当前职位 + 当前简历已有待审批或终态 action 时记为 `skipped(already_persisted)`。
- [ ] AC3: 每个入库职位自动跑匹配；`communicate` 自动 prepare 为 `approval_required`。
- [ ] AC4: `needs_review` / `skip` 不自动覆写、不自动审批、不自动执行，只进入结果队列。
- [ ] AC5: 连续 3 个 failed 触发 hard stop，剩余 pending 项标记为 `stopped`。
- [ ] AC6: 前端展示 per-item 结果、跳过原因、失败原因和审批入口；刷新后可通过 run id / resume 选择器恢复查看（实时逐条进度非 v0.1 承诺）。
- [ ] AC7: 单职位读取、现有 batch-loop、human-review override、审批 execute 流程不回归。
- [ ] AC8: 后端测试、userscript 测试页、前端 lint/type/build、BOSS e2e smoke 均通过或记录明确未运行原因。

## Out of Scope

- 自动发送“立即沟通”。
- 自动投递简历。
- 自动 approve 或批量 approve。
- 无限滚动抓取、搜索条件自动调参、跨页全站抓取。
- 新标签页自动接管。
- 点击标题链接 `a.job-name`、`a.more-job-btn` 或 pane 内任何按钮
  （`.op-btn-chat` / `.op-btn-like`）。
- 采集招聘者信息（`.job-boss-info`）。
- 城市首页（`/foshan/` 等）与其他非 geek 推荐列表 DOM 形态。
- v0.1 实时逐条进度与可用暂停（留给异步版本）。
- 替代现有 batch-loop；本任务是 discovery 前置层。
- 绕过验证码、限流或登录态检测。
