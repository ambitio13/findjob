# JD 抓取字段补全与 needs_review 草稿开场白

## Background

08-12 用户在真实 BOSS 页跑 dry-run(人审覆写路径已上线、并成功完成一次真实
发送),发现三个体验缺口:

1. **JD 抓取不到公司名称**:`docs/boss-userscript.user.js` 的
   `extractBossRecommendedJobV1` 使用 `.company-name` / `.boss-name` /
   `[class*="company-name"]` 选择器,与 BOSS 推荐职位详情页真实 DOM 不匹配,
   后端 `upsert_job_from_browser_jd` 兜底写成 `(未知公司)`。
2. **JD 抓取不到岗位地点**:地点依赖
   `.job-info li, .tag-list li, .info-primary li, .job-detail .info li` 的
   启发式解析,选择器同样失配,`infoTexts` 为空,`location` 恒为 null。
3. **needs_review 时人审区没有草稿可预填**:`draft_opening_message` 链路
   (08-12-human-review-override)已接线,但
   `boss_match_system.md` 明确要求 "Set opening_message to null when decision
   is skip or needs_review",模型从不生成草稿,人审只能从零手写。

影响链:公司/地点缺失 → 匹配 prompt 的 JOB DESCRIPTION 段
(`company:` / `location:`)为空 → 模型无法判定城市/公司匹配 → risks 与
missing_requirements 增多 → needs_review 进一步泛滥。字段补全与人审草稿是
同一体验链路的上下游。

## Goal

让 JD 抓取在真实 BOSS 详情页稳定捕获公司与地点,让 needs_review 匹配天然
携带一份可预填的草稿开场白(仅供人审编辑,不改变任何发送护栏)。

## Requirements

- **R1 userscript 公司/地点选择器修复**:诊断先行——在真实详情页采集实际
  DOM 结构,再扩充 `extractBossRecommendedJobV1` 的公司选择器回退链与地点
  解析;选择器 profile 版本保持 v1(仅适配 DOM,不改输出 schema)。
- **R2 地点启发式改进**:地点 li 先按空白/换行切分再判定,避免
  「城市 + 经验」混合条目被整条跳过;补充城市/行政区正向模式;直接选择器
  优先,启发式保底。
- **R3 匹配 prompt 放开 needs_review 草稿**:`boss_match_system.md` 改为
  decision 为 communicate 或 needs_review 时都生成开场白(同一套 10–500 字、
  中文、无 PII、无夸大规则);仅 skip 保持 null。prompt 中标注 needs_review
  的草稿是「供人工审阅的 tentative draft」。
- **R4 安全不变量**:安全门 `apply_match_safety_gate` 逻辑零改动(needs_review
  原样透传);发送路径仍必须 prepare(human_review) → approve → execute;
  skip 永不产生消息;semi-auto 行为不变。
- **R5 semi-auto 人审交接(08-12 补充,用户 dry-run 反馈)**:半自动匹配到
  needs_review 停车后,人审区必须可见并自动展开(原实现用 `!semiAuto`
  门槛导致半自动停车后无人审入口,形成死胡同)。不变量细化为:自动路径
  永不提交 human_review(覆写只由人显式点击发出),而非“半自动时不展示
  人审区”。
- **R6 skip 开放人审覆写(08-12 补充,用户 dry-run 反馈)**:截图现场为
  skip(城市明确不匹配)且无人审出口。原“skip 禁止覆写”是保守范围决策
  而非安全必需——真正护栏是消息复验 + approve + execute 人工 + 审计。
  现 skip 与 needs_review 同为人工担责路径,UI 用更强警告区分;
  communicate + 覆写仍 422。

## Acceptance Criteria

- [x] AC1:真实 BOSS 详情页抓取后,`job.company` 与 `job.location` 在库中
      为真实值(不再是 `(未知公司)` / null)。
- [x] AC2:userscript 公司选择器回退链与地点解析覆盖诊断发现的真实 DOM;
      抓取失败时字段仍为 null(不写入垃圾值)。
- [x] AC3:prompt 修改后 `pytest -q` + `ruff check` 全绿(golden set 门逻辑
      未动,不得回归);skip → opening_message 仍为 null。
- [x] AC4:真实匹配一条 needs_review 职位,响应 `draft_opening_message`
      非空,前端人审区预填该草稿(用户现场验证)。
- [x] AC5:安全回归:needs_review 无 human_review 的 prepare 仍 422;
      communicate 直通路径与 semi-auto 自动发送行为不受影响。
- [x] AC6:semi-auto 匹配到 needs_review → 循环停止、人审区自动展开且草稿
      预填;前端 `pnpm lint && pnpm type-check && pnpm build` 全绿。
- [x] AC7:skip + 有效人工消息 + acknowledged → prepare 成功(outgoing_text
      为人工消息、source_snapshot 含审计键);communicate + 覆写仍 422;
      前端 skip 也渲染人审区(更强警告);后端 `pytest -q` + `ruff` 全绿。

## Constraints

- 只改 userscript 选择器/启发式与一份 prompt 模板;不改安全门、不改 prepare
  契约、不加 alembic 迁移。
- 选择器必须基于真实页面诊断结果,不得凭空猜测后直接上线。
- 草稿消息与正式消息共用 `validate_opening_message` 规则;草稿只进入人审
  textarea,任何自动路径不得使用。

## Out of Scope

- 安全门阈值校准(missing_requirements 规则放宽)另立任务。
- 推荐列表卡片上的公司/地点展示增强。
- userscript 自动化测试基建重构(仅按需补抽取用例)。
