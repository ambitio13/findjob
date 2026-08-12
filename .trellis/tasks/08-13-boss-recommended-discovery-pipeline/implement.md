# BOSS 推荐列表发现与采集流水线 Implementation Plan (v2)

## Checklist

- [x] 真机 probe 取证（`probe-evidence.md`）：master-detail 零导航、卡片/pane
  selector、禁点/禁采清单、无沟通徽标。
- [x] Read implementation context:
  - `.trellis/spec/backend/evolution-contracts.md`
  - `.trellis/spec/shared/code-quality.md`
  - `.trellis/spec/shared/typescript.md`
  - `docs/boss-local-dev-runbook.md`
  - `docs/manual-boss-pilot.md`
  - `docs/boss-anti-automation-findings.md`
  - `.trellis/tasks/08-03-boss-recommended-list-batch-loop/design.md`
  - `.trellis/tasks/08-12-human-review-override/prd.md`
  - 本任务 `probe-evidence.md`
- [x] Add userscript result schema for sanitized job candidates
  (`job_key`/`rank`/`title`/`company`/`salary`/`location`/`tags`/
  `candidate_hash`；无 communicated 标志)。
- [x] Add bridge op kinds and schema fields for `scan_visible_jobs`,
  `open_job_by_key`（含 `expected_list_url_hash`）, `wait_job_detail_ready`
  （含 `expected_title`）。
- [x] Extend `docs/boss-userscript.user.js`:
  - `boss_recommended_list_v1` scan profile（证据 §2 selector，fallback chain）。
  - `scan_visible_jobs` 明确区分 `not_recommended_list_page`（列表容器缺失）
    与合法空列表 `no_visible_jobs`；只返回/持久化 `limit` 个候选。
  - `lastScanCandidates` 内存缓存（URL 变化或新 scan 清空）。
  - `open_job_by_key` 只点卡片根节点；点击后校验 URL 未变，变化即
    `unexpected_navigation`；不承诺从当前 tab 检测新窗口，只通过禁点和
    pilot 兜底。
  - `wait_job_detail_ready` 比对 `.job-header-info` 文本与 `expected_title`。
  - 新增 pane 读取 profile `boss_recommended_pane_v1`（证据 §3）；白名单排除
    `.job-boss-info`、`.c-job-tools`、`.c-hot-link`、`.c-breadcrumb`、
    `.job-detail-op`。
- [x] Add/update userscript tests page（`docs/boss-userscript-tests-discovery.html`）：
  fake 列表 + fake pane，覆盖 scan/open/wait/read 与禁点断言。
- [x] Extend `upsert_job_from_browser_jd` with optional
  `external_id_override`（discovery 传 `job_key`；inspect 路径不传，行为不变）。
- [x] Add backend service `boss_recommended_discovery_service.py`：
  零导航串行流程、per-item commit、`already_persisted` 业务跳过（仅当前
  job+resume 已有可复用 communicate action / terminal result 时跳过；单纯
  `is_new_job=False` 仍继续 match/prepare）、
  连续 3 failed hard stop、run 级排他（batch-loop/discovery 互斥 → 409
  `active_conflict`）、channel 锁 sentinel `discovery:<run_id>`。
- [x] Extract/reuse a public helper for reusable communicate actions
  (`application_id` + `user_id` + `boss_immediate_communicate`), instead of
  duplicating private `_find_active_communicate_action` SQL; include terminal
  external-result rows in the reusable check.
- [x] Define `AgentRun.status` mapping to mirror batch-loop:
  completed→`succeeded`, hard_stopped/failed→`failed`, with
  `result["discovery_status"]` carrying the workflow-specific status.
- [x] Keep the channel sentinel lock alive for the whole run: do not call
  `channel.clear()` between item ops unless immediately re-setting
  `set_active_application("discovery:<run_id>")`; clear once on final cleanup.
- [x] Implement crash-recovery resume by re-running `scan_visible_jobs` to
  rebuild `lastScanCandidates`; if rescan fails or pending keys are absent,
  mark non-terminal items `stopped(interrupted)` rather than causing repeated
  `candidate_not_found` failures.
- [x] Add backend router `boss_recommended_discovery.py`；静态段先于参数化
  路由注册；`limit` 默认 3 上限 10；`mode` 仅 `prepare_only`，
  `auto_execute` 在 API 层经 dry-run gate 拒绝。
- [x] Reuse existing match decision / communicate prepare services；不复制
  业务规则。
- [x] Add backend tests:
  - bridge sanitization（白名单外键剥离、raw href/联系人姓名丢弃）
  - `not_recommended_list_page` vs valid empty scan (`no_visible_jobs`)
  - per-item success prepare-only
  - `is_new_job=False` 但当前 resume 无可复用 action → 继续 match/prepare
  - 当前 job+resume 已有可复用 communicate action / terminal result →
    `skipped` + `skip_reason=already_persisted` + `failure_code=null`，并重置连续失败计数
  - needs_review/skip handling
  - `unexpected_navigation` / `page_mismatch` hard stop
  - hard stop after 3 failed
  - cross-user 404；active run 互斥 409
  - `AgentRun.status` mapping matches batch-loop semantics
  - sentinel lock is not cleared between item ops
  - resume re-scan rebuilds cache or marks interrupted items stopped
  - auto_execute rejected while gate not passed
  - upsert `external_id_override` 不影响 inspect 路径（回归）
- [x] Add frontend API client/types（nullable 字段区分，见 shared/typescript）。
- [x] Add `RecommendedDiscoveryPanel`：bridge 状态、resume 选择器、limit
  stepper（默认 3）、开始按钮、结果表、审批链接；v0.1 隐藏暂停按钮。
- [x] Wire panel into the BOSS Pilot / batch area without disturbing existing
  application detail tabs.
- [x] Update docs/runbook with the discovery pilot path（limit 3、prepare-only、
  零导航确认清单）。

## Validation

```bash
cd backend
./.venv/bin/ruff check app
./.venv/bin/pytest -q
```

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

```bash
./scripts/e2e-smoke.sh --skip-up
```

Manual pilot:

- Open BOSS `/web/geek/jobs` with userscript connected.
- Run discovery with `limit=3`.
- Confirm 3 or fewer items reach terminal states.
- Confirm the page never navigates and no new window opens during the run.
- Confirm `communicate` items create `approval_required` actions only.
- Confirm no message is sent and no resume is submitted.

## Review Gates

- No external side effect happens before explicit approval.
- No new arbitrary selector/evaluate surface is exposed to backend or frontend.
- `scan_visible_jobs` does not widen the prod probe whitelist; `open_job_by_key`
  must never be reachable from the diagnostic probe endpoint.
- The new page-text-returning scan/read ops carry an explicit privacy review:
  short sanitized fields only, no raw URLs, raw HTML, contact names, chat
  previews, cookies, tokens, or storage.
- 禁点：`a.job-name`、`a.more-job-btn`、`.op-btn-chat`、`.op-btn-like`；
  禁采：`.job-boss-info`。测试页需有断言。
- No raw URL, raw HTML, cookies, tokens, contact names, or chat content are
  persisted or logged.
- Existing single-job inspect and existing batch-loop tests still pass.
- `needs_review` / `skip` / `already_persisted` are not incidents and do not
  auto-submit human_review; `already_persisted` is represented as
  `skip_reason`, not `failure_code`.
- Cross-path duplicate JobPosting rows (`page_url_hash` inspect vs `job_key`
  discovery) are an accepted v0.1 tradeoff and must not be "fixed" with a
  weak title/company merge during implementation.

## Rollback

- Disable the discovery panel route/entry.
- Remove discovery router registration.
- Existing userscript `read_jd`（detail profile）、single-job inspect、
  batch-loop、execute flows must remain independently usable.
