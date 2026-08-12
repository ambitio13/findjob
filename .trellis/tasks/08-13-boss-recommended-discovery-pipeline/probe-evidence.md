# Probe Evidence — BOSS 推荐列表页（/web/geek/jobs）

取证日期：2026-08-13。工具：`docs/boss-discovery-probe.user.js`（只读油猴探针，
运行于用户真实已登录 Chrome，无 CDP/自动化特征）。原始 JSON 由用户人工粘贴回传，
本文件只保留结构化结论与脱敏计数，不含原始 URL、不含个人信息。

## 0. 取证方式备注

- 尝试过 chrome-devtools MCP / 独立浏览器实例：无登录态且被 Boss 风控标记
  （页面加载后被销毁为 about:blank，命中 docs/boss-anti-automation-findings.md
  案例 3/4）。已放弃，真机取证只能走 userscript 路线。
- 探针不点击、不导航、不上传；按钮/链接只统计 class 与 target。

## 1. 页面形态：master-detail，零导航

- 页面 `/web/geek/jobs`（推荐 tab）为左卡片列表 + 右内嵌详情 pane。
- 点击**卡片本体**（`div.job-card-wrap`）→ pane 原地切换，不转圈、不开新窗口，
  URL 全程不变；选中态以卡片 `active` class 标记（点击第二张卡后首卡 active 消失，
  两次探针报告互证）。
- 点击**标题链接** `a.job-name` → Boss JS 打开新窗口并整页加载
  `/job_detail/*.html`（用户实测）。该路径是陷阱，自动化不得触发。
- 首页 `/foshan/` 是另一套 DOM（`a.job-info`，126/127 为 `target="_blank"`），
  不在 v0.1 支持范围。
- pane 内 `.op-btn-chat`（立即沟通）点击行为（用户人工实测）：当前 tab
  导航到 `/web/geek/chat` 聊天页，列表页上下文立即销毁；chat 页无卡片、
  无 pane（探针 `pane_found=false`）。该按钮既是外部副作用入口又是同 tab
  导航陷阱，双重佐证禁点清单与 `unexpected_navigation` 硬停止的必要性。
  运行中若用户手动误点，下一个 op 会以 `page_mismatch` 安全硬停止，失败
  可解释。

## 2. 卡片列表 DOM 模型

- 卡片根：`div.job-card-wrap`（`[class*=job-card]` 计数 45 含子元素；
  `.job-card-wrapper`/`.job-card-body` 均不存在）。
- 标题链接：`a.job-name`（16 个，16/17 无 target；唯一 `_blank` 视为异常位）。
  父链：`a.job-name < div.job-title clearfix < div.job-info`。
- 字段 selector（计数=16 卡片）：
  - title: `.job-name`
  - salary: `.job-salary`（注意不是 `.salary`）
  - company: `[class*=company]`（无 `.company-name`）
  - location: `[class*=area]`（15，个别卡缺失）
  - tags: `.tag-list li`（72）
- 沟通徽标：不存在（`[class*=communicate]`=0，`继续沟通/已沟通` 文本=false）。
  → scan 期无法检测已沟通/已入库，只能在 upsert 后以 `is_new_job=False` 判定。
- job_key 派生：`sha256(a.job-name href 的路径部分)`，跨 scan 稳定。

## 3. 内嵌详情 pane DOM 模型

容器链：`div.job-detail-container < div.recommend-result-job clearfix <
div.recommend-result-inner < div.job-recommend-result < div.page-jobs-main`。
隔离规则：pane = 含“职位描述”文本且不包含 `.job-card-wrap` 的最大容器。

```text
div.job-detail-container
└─ div.job-detail-box
   ├─ div.job-detail-header
   │  ├─ div.job-header-info          ← 职位标题 + 薪资（readiness 比对源）
   │  └─ div.job-detail-op clearfix   ← .op-btn-like(收藏) / .op-btn-chat(立即沟通)
   └─ div.job-detail-body
      ├─ h3.title                     ← 文案“职位描述”小节标题，不是职位标题！
      ├─ ul.job-label-list            ← 标签 li
      ├─ p.desc                       ← JD 正文（~500 字）
      ├─ div.job-boss-info            ← 招聘者信息（含人名）→ 禁采
      ├─ div.job-address              ← 工作地点
      └─ a.more-job-btn               ← 查看更多信息（会导航）→ 禁点
```

pane 外同级噪音（读取时必须排除）：`div.c-job-tools`、`div.c-hot-link`、
`div.c-breadcrumb`。

字段 selector（pane profile `boss_recommended_pane_v1`）：

- title/salary: `.job-detail-header .job-header-info`（内含 `[class*=salary]`=1）
- description: `.job-detail-body p.desc`
- tags: `.job-detail-body ul.job-label-list li`
- location: `.job-detail-body .job-address`
- company: pane 内无（`[class*=company]`=0）→ 取自卡片侧
- readiness: `.job-header-info` 文本包含被点卡片标题

## 4. 对设计的直接结论

1. P0（新标签空转）不成立：卡片本体点击零导航。v0.1 流水线形态定为
   scan → 点卡片本体 → 等 pane 标题匹配 → 读 pane → upsert → match → prepare。
2. URL 不变 → `page_url_hash` 不能做 dedup 键；discovery 路径
   `external_id = job_key`（upsert 服务需接受显式 external_id 覆写；
   单职位 inspect 路径保持 page_url_hash 不变）。
3. `open_job_by_key` 只点卡片根节点；`a.job-name`、`a.more-job-btn`、
   `.op-btn-chat`、`.op-btn-like` 一律不点。
4. scan 缓存与 bridge 页绑定全程存活 → 不需要 go_back/重扫/多 tab 编排。
5. 隐私：`.job-boss-info` 禁采；pane 读取 profile 的 sanitize 白名单不含招聘者字段。
