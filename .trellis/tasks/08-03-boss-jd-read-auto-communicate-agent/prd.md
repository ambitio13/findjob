# BOSS 推荐职位 JD 读取与自动沟通 Agent

## Goal

在 BOSS 直聘推荐职位页面上跑通一个受控闭环：

1. 用户手动打开 BOSS 推荐职位或职位详情页；
2. 后端 agent 通过油猴桥接读取当前职位 JD；
3. 后端用用户画像、简历、偏好和 JD 做匹配判断；
4. 匹配通过时生成开场白；
5. 后端通过油猴桥接点击「立即沟通」并填入/发送开场白；
6. 全过程记录 application/action/agent_run/timeline，失败时硬停止并给出恢复路径。

本任务是规划任务，目标是让后续 coding agent 可以按子任务开工。功能实现不是本任务范围。

## Confirmed Facts

- BOSS 直聘会检测 CDP/Playwright 协议层自动化；项目已选择 Tampermonkey userscript bridge 作为 BOSS 真实浏览器接触方式。
- 当前桥接任务 `08-03-userscript-bridge-adapter` 已规划并实现基础 `fill/click/check_visible/count/read_title/read_url/read_content` 指令。
- 现有规约要求后端保留业务逻辑，油猴脚本只执行后端下发的页面指令。
- 现有自动投递基础强调审批、幂等、审计、失败 envelope 和硬停止，不允许裸脚本直接批量投递。
- 新闭环需要读取 JD，因此必须把「不传原始 JD」的默认桥接规约改成「默认不传页面正文；只有显式 `read_jd` 能力可以读取职位正文，且受限、脱敏、可审计」。

## Product Scope

### In Scope

- 只支持 BOSS 直聘，且优先从推荐职位页面或职位详情页读取单个当前职位。
- 通过油猴脚本读取 JD 文本和职位关键字段，不读取 raw HTML、cookies、tokens、localStorage、完整页面 dump。
- 后端负责 JD 解析、岗位匹配、开场白生成、审批/幂等/审计判断。
- 油猴脚本负责页面内动作：读取页面字段、点击「立即沟通」、填入输入框、点击发送。
- 先跑通「一个职位一次闭环」，用户可手动切到下一个职位；大循环和批量扫推荐职位后续再做。
- 所有异常都有确定分类：可重试、需用户处理、需人工对账、不可自动继续。

### Out of Scope

- 不做自动翻页、自动滚动扫全量推荐列表、批量投递。
- 不绕过 CAPTCHA、登录风控、滑块、短信验证、平台限流。
- 不让油猴脚本自己决定匹配、生成文案、记录投递状态。
- 不在油猴脚本中硬编码业务规则、简历内容、用户偏好或 prompt。
- 不用 CDP/Playwright 作为 BOSS 主路径。

## Why Not Pure Tampermonkey Automation

纯油猴脚本当然可以「读取页面、判断关键词、点立即沟通、发固定话术」，但它不适合作为本项目的自动投递 agent 主体：

- **缺少审计**：脚本点了什么、为什么点、用了哪个 JD、哪版简历、哪个 prompt，很难形成后端统一 timeline。
- **缺少幂等**：刷新、重复安装、多标签页、脚本重启都可能重复沟通同一岗位。
- **缺少审批边界**：外部副作用需要绑定「用户批准的精确 payload」，浏览器脚本很难和后端的 approval/action 机制一致。
- **缺少失败恢复**：CAPTCHA、登录过期、选择器漂移、发送结果未知时，纯脚本容易继续误操作或丢状态。
- **缺少多平台抽象**：BOSS 只是第一个平台，业务决策应属于 `PlatformAdapter` 和 service 层，不应散落在某个网站脚本里。
- **缺少安全边界**：把用户画像、简历、prompt、策略下放到第三方页面上下文，会扩大泄漏面。
- **缺少测试面**：后端契约、状态机、错误矩阵、prompt 版本和结果 schema 都更适合在后端测试。

因此最终决策是：**油猴脚本是页面内执行器，不是业务 agent。后端 agent 决策，油猴执行最小动作。**

## Requirements

### R1. BOSS JD 读取能力

- 新增桥接指令能力 `read_jd`，只允许读取当前 BOSS 职位页/推荐卡片中的职位正文和关键字段。
- `read_jd` 返回结构化文本，不返回 raw HTML：
  - `title`
  - `company`
  - `location`
  - `salary`
  - `experience`
  - `education`
  - `skills`
  - `description`
  - `page_url_hash`
  - `source_kind`
- JD 文本必须有长度上限和脱敏处理，不能携带 cookies、tokens、手机号验证码、页面脚本或完整 DOM。
- 如果当前页不是明确的 BOSS 职位页/推荐职位卡片，返回 `page_not_supported` 并硬停止。

### R2. JD 匹配决策在后端

- 后端把 `read_jd` 结果写入或关联到内部 Job/JD 分析流程，保留来源和时间。
- 匹配输入必须至少包含：当前 JD、用户画像/求职意向、选定简历版本、可选黑名单/偏好规则。
- 匹配输出必须结构化：
  - `match_decision`: `communicate | skip | needs_review`
  - `score`
  - `reasons`
  - `risks`
  - `missing_requirements`
  - `recommended_opening_message`
- 低置信度、关键字段缺失、明显不匹配、薪资/城市/经验不符合时，不允许自动沟通。

### R3. 立即沟通动作必须可审计和幂等

- 点击「立即沟通」前必须创建 action draft，并绑定：
  - `application_id`
  - `job_source_hash`
  - `resume_version_id`
  - `opening_message`
  - `payload_hash`
  - `idempotency_key`
  - `decision_trace`
- 同一个 `platform + user + job_url_hash + resume_version + payload_hash` 不允许重复发送。
- 第一阶段可以采用「自动执行但有配置开关」或「人工确认后执行」两档；默认推荐人工确认，直到 10 次真实 dry-run 稳定。

### R4. 页面绑定和多标签安全

- 每条桥接指令必须绑定当前 page/session 标识或至少绑定预期 `page_url_hash`。
- 执行 `read_jd`、`click_immediate_communicate`、`send_message` 前都要校验当前页仍是同一个职位。
- 多个 BOSS 标签页同时安装油猴脚本时，不能让非目标标签消费指令。
- 页面 URL、标题、职位字段变化后，已生成开场白和 approval/action 必须变 stale。

### R5. 油猴脚本最小执行器

- 油猴脚本新增的能力限于：
  - `read_jd`
  - `click_immediate_communicate`
  - `fill_opening_message`
  - `send_opening_message`
  - `read_communication_result`
- 油猴脚本不得包含匹配算法、prompt、简历文本、用户画像、投递策略。
- 油猴脚本不得自动循环点击多个职位，不得自发执行动作；没有后端指令就什么也不做。

### R6. 失败优先

- 任何失败都必须映射为稳定错误码，并写入 timeline/agent_run/action。
- 硬停止场景：
  - 未登录或登录态过期；
  - CAPTCHA/滑块/短信验证；
  - 页面不是预期职位；
  - 多标签/页面绑定冲突；
  - JD 字段不足或解析异常；
  - 匹配结果 `skip` 或 `needs_review`；
  - 「立即沟通」按钮不可见或语义不明确；
  - 输入框不可定位；
  - 发送后结果未知；
  - 平台限流或重复沟通提示。
- `unknown` 结果不得自动重试点击，必须进入人工对账。

## Acceptance Criteria

- [ ] `prd.md` 明确回答「为什么不用纯油猴自动化投递」，并把油猴定位为执行器。
- [ ] `design.md` 定义后端 agent、adapter、bridge channel、userscript、application/action 的边界和数据流。
- [ ] `implement.md` 拆出可并行给 coding agent 的子任务，每个子任务都有验收点。
- [ ] 规约更新允许受限 `read_jd`，同时继续禁止 raw HTML/cookies/tokens/任意页面读取。
- [ ] 规约更新要求 BOSS 主路径为 userscript bridge，不再把 CDP 作为主要实现路径。
- [ ] 规约更新要求所有外部沟通动作具备页面绑定、幂等、审计和失败矩阵。
- [ ] `implement.jsonl` 和 `check.jsonl` 含真实上下文条目，可以通过 `task.py validate`。
- [ ] 本规划不启动实现，不要求提交真实沟通动作。
