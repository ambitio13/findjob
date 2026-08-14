# 实施计划:JD 抓取字段补全与 needs_review 草稿开场白

对应 prd.md 的 R1–R4 与 AC1–AC5。Phase A 为阻塞步骤(需要用户在真实 BOSS
页面执行诊断),Phase C 与 A/B 相互独立,可先做。

## Phase A:真实 DOM 诊断(阻塞,依赖用户)

- [x] A1 BOSS 详情页反调试封了控制台,诊断走桥:用户在本地后端在跑、
      userscript 在线、详情页为激活 tab 时,在终端执行 design.md §1 的
      probe 探针序列(`POST /userscript-bridge/probe` + probe_elements/
      read_jd),粘贴响应 JSON。
      已部分执行(08-12,agent 直跑):当前激活页是首页推荐流而非详情页。
      已探明推荐流卡片真实结构:`.look-job-list` →
      `.info-job`(`.name` 标题 + `p.salary`) +
      `.info-company`(`.company-logo-name` 公司 + `.company-location` 地点);
      `[class*="company"]`/`.job-card-wrap` 无命中。随后用户点开详情页,
      agent 重跑探针拿到详情页真实 DOM(见 A2)。
- [x] A2 诊断结论(详情页,均经 probe 验证):
      - 公司:`.sider-company .company-info`(探针返回「李陌茶」✓);裸
        `.company-name` 会先命中页底隐藏「看过该职位」推荐 span,必须后置;
      - 地点:`[class*="text-city"]`(探针返回「邯郸」✓);
      - 经验:`[class*="text-exper"]`(BOSS 自身拼错为 text-experiece,
        通配兼容;`.info-primary .text-experiece` 在部分职位页 0 命中);
      - 学历:`[class*="text-degree"]`(探针返回「本科」✓);
      - 标题:`.job-banner p.name` / `.info-primary p.name`(页面里
        `p.name` 是职位名,`span.name` 是公司/地点,靠 tag 区分);
      - 启发式 li 列表在新版页面上无输入,降级为兜底。

## Phase B:userscript 抽取修复(R1/R2 → AC1/AC2)

- [x] B1 `docs/boss-userscript.user.js` `extractBossRecommendedJobV1`:
      公司链改为 `.sider-company .company-info` → `.boss-name` →
      `[class*="company-name"]`(裸 .company-name 后置);标题链追加
      `.job-banner p.name` / `.info-primary p.name`。
- [x] B2 地点/经验/学历直选:`[class*="text-city"]` / `[class*="text-exper"]`
      / `[class*="text-degree"]` 优先;li 启发式改 token 级判定并修复
      「经验不限」误判学历。
- [x] B3 失配时字段保持 null(sanitizeJdField 对空文本返回 null);
      node --check 语法通过 + 离线启发式用例验证(混合 li 切分正确)。
- [x] B4 测试页无抽取函数接入,跳过 fixture 用例(按约定);验证以 D1
      真实页面为准。
- [x] B5 提醒用户更新 Tampermonkey 中的脚本内容。

## Phase C:匹配 prompt 放开 needs_review 草稿(R3/R4 → AC3/AC5)

- [x] C1 `backend/app/agents/prompts/templates/boss_match_system.md`:
      opening_message 规则改为 communicate 或 needs_review 均生成;
      needs_review 标注 tentative draft(供人审编辑);仅 skip 置 null。
- [x] C2 核对 `app/agents/prompts/boss_match.py` schema 行无需改动
      (`string|null` 兼容)。
- [x] C3 补 golden 用例 `needs_review_with_draft_message_passes_through`
      (prompt_regression_cases.json,20 条):needs_review + 合法草稿 →
      透传且消息保留,防未来回归。
- [x] C4 验证:`cd backend && set -a && source .env.test &&
      export QUEUE_NAMESPACE=job-search-agent-test && set +a &&
      .venv/bin/python -m pytest -q && .venv/bin/ruff check .`
      → 970 passed、ruff 全绿(08-12)。

## Phase D:真实链路验证 + 收尾(AC1/AC4 + spec)

- [x] D1 用户用更新后的 userscript 抓取一条真实详情页 → 核对
      `job.company`/`job.location` 入库为真实值(AC1)。
- [x] D2 用户真实匹配被拦截职位(needs_review/skip)→ 人审区可见且
      预填/手写路径可用,dry-run 完整发送成功(AC4)。
- [x] D3 安全回归核对:needs_review 无 human_review 的 prepare 仍 422
      (测试矩阵覆盖);communicate 直通与 semi-auto 行为不变,dry-run
      现场确认(AC5)。
- [x] D4 `trellis-update-spec`:沉淀「BOSS 详情页 DOM 适配诊断先行 +
      选择器 fallback 链演进」与「needs_review 草稿仅供人审、发送护栏不变」
      两条契约;勾选 prd.md 对应 AC;`python3 .trellis/scripts/task.py validate`。

## Phase E:semi-auto 人审交接(R5 → AC6,08-12 用户反馈插入)

- [x] E1 `RecommendedJobPilotPanel.tsx`:人审区渲染门槛去掉 `!semiAuto`
      (needs_review 恒展示);handleMatch 的 needs_review 分支在 semiAuto
      停车时 `setHumanReviewOpen(true)` 自动展开,交接点可见。
- [x] E2 不变量核对:human_review 仅由 `onHumanReviewPrepare` 人工点击发出,
      自动 effect(step 驱动)只触发 inspect/match/prepare,不提交覆写;
      approve/execute 永远人工。
- [x] E3 验证:`pnpm lint && pnpm type-check && pnpm build` 全绿(08-12);
      运行时行为随 D2/D3 现场核对。

## Phase F:skip 开放人审覆写(R6 → AC7,08-12 用户截图反馈插入)

- [x] F1 后端 `boss_communicate_service.py`:删除 skip+human_review 的 422
      分支;needs_review/skip 同走覆写路径,communicate+覆写仍 422;
      注释/docstring/schema docstring 同步。
- [x] F2 测试:`test_prepare_human_review_on_skip_returns_422` 改写为
      `test_prepare_human_review_override_skip_succeeds`(approval_required +
      outgoing_text=人工消息 + 审计键)。
- [x] F3 前端:人审区门槛 `needs_review || skip`;skip 用更强担责警告;
      handleMatch skip 分支 semiAuto 停车时同样展开人审区。
- [x] F4 验证:后端 970 passed + ruff 全绿;前端 lint/type-check/build 全绿。

## Review Gates

- A1 诊断输出未到手:不做 B1/B2,可先完成整个 Phase C。
- B 阶段任何选择器必须能对应到 A1 诊断输出中的真实 class。
- C4 不绿不得进入 D。
- D1/D2 为用户现场动作,不得以代码推断代替。

## 全局验证命令

```bash
cd backend && set -a && source .env.test && export QUEUE_NAMESPACE=job-search-agent-test && set +a && .venv/bin/python -m pytest -q && .venv/bin/ruff check .
```
