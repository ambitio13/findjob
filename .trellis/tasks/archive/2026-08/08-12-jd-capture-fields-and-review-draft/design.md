# 设计:JD 抓取字段补全与 needs_review 草稿开场白

## 0. 现状快照(已核实)

- userscript 唯一副本:`docs/boss-userscript.user.js`,抽取函数
  `extractBossRecommendedJobV1`(L182–272),输出 schema
  `{title, company, location, salary, experience, education, skills,
  description, source_kind, page_url_hash}`。
- 公司选择器:`.company-name` → `.boss-name` → `[class*="company-name"]`,
  均失配 → `company=null` → 后端 `upsert_job_from_browser_jd` 兜底
  `(未知公司)`(boss_recommended_job_service.py L186)。
- 地点:`.job-info li, .tag-list li, .info-primary li, .job-detail .info li`
  收 li 文本 → 启发式;选择器失配时 `infoTexts=[]` → `location=null`。
  且启发式整条判定:li 同时含城市与「经验/学历」字样时会被排除正则误杀。
- prompt `boss_match_system.md` L41–49:opening_message 规则标注
  "only when decision is communicate",L49 明确 needs_review/skip 置 null。
- 后端链路已就绪:`draft_opening_message=raw_opening_message`
  (api/v1/boss_match.py L97,门前原始消息恒带出);安全门对非 communicate
  原样透传(opening_message_guard.py L121–122),needs_review 携带的消息会
  完整保留;前端人审区已用 `draft_opening_message` 预填
  (RecommendedJobPilotPanel.tsx L281–282)。
- 匹配 user prompt 把 `company:` / `location:` 注入 JOB DESCRIPTION 段
  (prompts/boss_match.py L122–131)——字段缺失直接劣化匹配判断。

结论:**纯前端(userscript)选择器适配 + 一份 prompt 模板修改**,后端零逻辑
改动、无迁移。

## 1. Phase A:真实 DOM 诊断(阻塞步骤,走桥不走控制台)

**约束更新**:BOSS 详情页反调试强,DevTools 控制台不可用。诊断改走既有
数据面:`POST /api/v1/userscript-bridge/probe`(仅 dev 可用,prod 403),
`op=probe_elements` 由 userscript 在真实页面执行,返回元素的
`tag/class/id/aria-label/text/visible`(≤20 个元素、8000 字符 JSON)。

前置:本地后端在跑、userscript 心跳在线、BOSS 详情页为当前激活 tab
(不传 page_id 时指令自动绑定激活页)。local 环境未设
`BOSS_BRIDGE_CHANNEL_TOKEN` 时无需 token;设置了则带 `X-Bridge-Token`。

诊断探针序列(在终端 curl,无需控制台):

```bash
BASE=http://localhost:8000/api/v1/userscript-bridge/probe
probe() { curl -s -X POST "$BASE" -H 'Content-Type: application/json' \
  -d "{\"op\":\"probe_elements\",\"selector_kind\":\"css\",\"selector_value\":\"$1\"}"; echo; }

probe '[class*="company"]'
probe '[class*="sider"]'
probe '.job-banner, .job-info, .info-primary'
probe '[class*="location"], [class*="city"], [class*="area"]'
probe '.job-detail .info li, .info-primary li, .tag-list li'
curl -s -X POST "$BASE" -H 'Content-Type: application/json' \
  -d '{"op":"read_jd","selector_profile":"boss_recommended_job_v1"}'; echo
```

最后一条 `read_jd` 核对当前抽取基线(company/location 应为 null,
title/description 应非空)。通配选择器命中后,响应里的 `class` 字段即真实
class 名,足以定出精确选择器。

产出物:探针响应 JSON 粘贴回任务 notes。**未诊断不得写死新选择器。**

## 2. Phase B:userscript 抽取修复

### 2.1 公司选择器回退链(R1)

保留现有三项,按诊断结果追加候选(示例形态,最终以诊断为准):

```
.company-info .name / .sider-company .name / .job-company .name
/ [class*="company"] .name / .company-info a
```

取值仍走 `sanitizeJdField(..., 200)`;全部失配保持 null(不写垃圾值,AC2)。

### 2.2 地点解析(R2)

两级策略:

1. **直接选择器优先**:按诊断结果加地点专用选择器(示例形态
   `.job-info .location` / `[class*="location"]`,以诊断为准)。
2. **启发式兜底改进**:`infoTexts` 中每条先按空白/换行切成 token,逐 token
   判定——token 为中文、长度 ≤ 12、不含
   `/经验|学历|本科|硕士|博士|大专|高中|初中|不限|年|薪|K/` 字样时记为
   地点;修复「城市+经验混合 li 被整条误杀」的问题。经验/学历判定同样改为
   token 级。skills/salary/description 逻辑不动。

### 2.3 测试与分发

- `docs/boss-userscript-tests.html` 现状无抽取用例;若页面可访问抽取函数,
  补一条 fixture 用例(mock 详情页 DOM → 断言 company/location 非空);
  无法访问则 AC 以真实页面验证为准,不硬造基建。
- userscript 是用户手动安装的 Tampermonkey 脚本,改完提醒用户更新脚本内容。

## 3. Phase C:匹配 prompt 放开 needs_review 草稿(R3)

`backend/app/agents/prompts/templates/boss_match_system.md` 修改
opening_message 段:

- 标题改为 "opening_message rules (when decision is "communicate" or
  "needs_review"):";
- 追加一句:decision 为 needs_review 时,opening_message 是一份 **tentative
  draft**,供人工审阅者编辑或替换,同样必须遵守长度/中文/无 PII/无夸大规则;
- L49 改为 "Set opening_message to null only when decision is "skip".";
- schema 行(`boss_match.py` 的 `"opening_message": string|null`)不变。

### 安全性论证(R4)

- 安全门只处理 communicate(opening_message_guard.py L121),needs_review
  带消息原样透传,不会升级为 communicate;
- 草稿只出现在 `draft_opening_message` → 人审 textarea;发送必须经过
  prepare(human_review) → approve → execute,与 08-12-human-review-override
  契约一致;skip 仍为 null,不存在 skip 携带消息的路径;
- semi-auto 循环遇 needs_review 即停,永不携带 human_review,行为不变;
- golden set 测的是纯门函数,门逻辑零改动,不受 prompt 变化影响。

### 后端测试

- 既有 `pytest -q` 全绿(尤其 `test_prompt_regression.py` golden set);
- 补一条门单测:needs_review + 合法消息 → 透传且消息保留(若既有用例已覆盖
  则只核对不重复)。

## 4. Phase D:真实链路验证 + spec 同步

1. 用户重装 userscript → 真实详情页抓取 → 核对 `job.company`/`job.location`
   入库(AC1)。
2. 真实匹配一条 needs_review 职位 → `draft_opening_message` 非空 → 人审区
   预填(AC4);顺带观察公司/地点补齐后 missing_requirements 是否收敛。
3. 回归核对 AC3/AC5(prepare 422 矩阵、communicate 直通、semi-auto)。
4. `trellis-update-spec`:把「BOSS 详情页 DOM 适配需诊断先行、选择器以
   fallback 链演进」与「needs_review 草稿仅供人审、发送护栏不变」写入
   相应 spec(backend/evolution-contracts.md 或 frontend 层)。

## 5. 风险与回滚

- 选择器再失配:字段回落 null,后端兜底语义不变,无破坏性;再走一次诊断。
- prompt 放开后模型对 skip 误带消息:门对 skip 同样透传,但 prepare 契约
  只认 communicate/needs_review+覆写,skip 无发送路径;golden/单测兜底。
- 回滚:还原 userscript 文件与 prompt 模板即恢复,无状态迁移。
