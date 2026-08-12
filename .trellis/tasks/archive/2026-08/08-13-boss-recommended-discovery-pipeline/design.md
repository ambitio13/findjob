# BOSS 推荐列表发现与采集流水线 Design (v2 — evidence-backed)

## Summary

This task adds a discovery layer in front of the existing BOSS batch-loop.
The system behaves like a deterministic crawler workflow with one LLM
decision node, not a free-form autonomous agent.

v2 incorporates real-page probe evidence (`probe-evidence.md`, 2026-08-13).
The BOSS geek recommend page (`/web/geek/jobs`) is a master-detail layout:
clicking a card body switches an inline detail pane **without navigation**.
The v0.1 pipeline is therefore zero-navigation:

```text
scan_visible_jobs -> open_job_by_key (click card body, pane switches in place)
-> wait_job_detail_ready (pane header title == clicked card title)
-> read_jd (pane profile) -> upsert (external_id = job_key) -> match -> prepare
```

The user remains in control of every external side effect. The workflow may
read and prepare; it must not approve, send, or submit.

## Evidence Base

- `probe-evidence.md` in this task directory: three probe rounds over the
  real logged-in page via `docs/boss-discovery-probe.user.js` (read-only
  userscript; no CDP, no clicks, no uploads).
- Key facts driving this design:
  - Card body click = in-place pane switch; URL never changes.
  - Title link `a.job-name` opens a NEW WINDOW via Boss JS — a trap the
    automation must never trigger.
  - The pane has no company field and no recruiter-free guarantee except by
    excluding `.job-boss-info` (recruiter PII, forbidden to collect).
  - No communicated/already-applied badge exists on the list page.
  - City home page (`/foshan/`) uses a different DOM (`target="_blank"`
    links) and is out of scope.

## Existing Boundaries To Reuse

- `docs/boss-userscript.user.js` remains the only BOSS page capability layer.
  Extend it; do not create a second agent-facing userscript. (The probe
  script is a dev-time evidence tool only, never shipped to the bridge.)
- `app.platforms.boss.userscript_channel` remains the bridge data plane:
  process-local queue, page_id binding, expected URL hash, channel token,
  sanitized results.
- `app.services.boss_recommended_job_service.inspect_current_job` /
  `upsert_job_from_browser_jd` own JD upsert semantics. **Extension point:**
  the upsert path must accept an explicit `external_id` override so the
  discovery path can key by `job_key` instead of `page_url_hash` (see
  Identity & Dedup). The single-job inspect path keeps `page_url_hash`.
- `app.services.boss_match_service.run_boss_match_decision` owns the LLM
  matching node.
- `app.services.boss_communicate_service.prepare_communicate_action` owns
  communicate action preparation and approval payload hashing.
- `app.services.boss_batch_loop_service` expresses per-item batch progress in
  `AgentRun.result`; discovery mirrors this shape.

## Architecture

```text
Browser/BOSS page (/web/geek/jobs, master-detail)
  └─ Tampermonkey userscript
      ├─ scan_visible_jobs      (read card list; derive job_key locally)
      ├─ open_job_by_key        (click card ROOT only; pane switches)
      ├─ wait_job_detail_ready  (pane header title == expected title)
      └─ read_jd                (pane profile boss_recommended_pane_v1)

Backend bridge
  └─ userscript_channel
      ├─ page_id binding (stable: no navigation ever happens)
      ├─ expected_url_hash binding (list URL hash, invariant)
      └─ sanitized InstructionResult

Backend workflow
  └─ boss_recommended_discovery_service
      ├─ create AgentRun(workflow_type=boss_recommended_discovery)
      ├─ scan candidates (persist items with job_key)
      ├─ per item: click card -> wait pane -> read pane -> upsert(job_key)
      ├─ run match decision
      ├─ prepare communicate action when allowed
      └─ persist per-item status after each transition

Frontend
  └─ RecommendedDiscoveryPanel
      ├─ connection and page-state indicators
      ├─ limit/start controls (pause deferred, see Execution Model)
      ├─ result table with per-item status
      └─ links into existing approval/detail flows
```

## Userscript Contract

### Op: `scan_visible_jobs`

Read-only scan of visible `div.job-card-wrap` cards.

Result per candidate:

```json
{
  "job_key": "sha256:<hash of a.job-name href path>",
  "rank": 1,
  "title": "后端开发工程师",
  "company": "某公司",
  "salary": "20-30K",
  "location": "上海",
  "tags": ["Python", "Golang"],
  "candidate_hash": "sha256:..."
}
```

Rules:

- `job_key` = sha256 of the `a.job-name` href **path** (query stripped),
  computed locally; the raw href is never returned.
- Field selectors (evidence §2): `.job-name` title, `.job-salary` salary,
  `[class*=company]` company, `[class*=area]` location, `.tag-list li` tags.
  Unmatched fields stay `null` (fallback-chain convention, spec §13).
- No `status_flags.communicated`: the page has no such badge. Do not invent
  one. Duplicate/already-persisted detection happens post-upsert only.
- Same strip/secrets/cap sanitization rules as JD fields; no raw href, no
  raw HTML, no contact names.
- Page classification is explicit: if the geek recommendation list container
  (`div.job-card-wrap` candidates inside the master-detail page) is absent,
  return `success=false, error=not_recommended_list_page`. If the container
  shape is present but there are no visible candidates after applying
  `max_items`, return `success=true` with an empty `job_candidates` list and
  the run message `no_visible_jobs`.
- Return and persist at most `max_items` candidates. v0.1 should not create
  permanent pending rows for off-limit candidates.
- Keep in-memory `lastScanCandidates`: `job_key -> card element`. Cleared on
  URL change or new scan. (With zero navigation the cache survives the run.)

### Op: `open_job_by_key`

Click one cached card so the inline pane switches.

Instruction fields: `op`, `job_key`, `expected_list_url_hash`.

Rules:

- Refuse if `job_key` not in `lastScanCandidates` (`candidate_not_found`).
- Refuse if current URL hash differs from `expected_list_url_hash`
  (`page_mismatch`).
- Click the card ROOT element (`div.job-card-wrap`) only. Never click
  `a.job-name`, `a.more-job-btn`, `.op-btn-chat`, `.op-btn-like`, or any
  descendant anchor/button. If the click implementation cannot guarantee
  root-only targeting, dispatch a synthetic click on the root node.
- After click, verify `location.href` did not change; if it changed,
  return `success=false, error=unexpected_navigation` (the backend hard-stops).
- A new tab/window opened by the browser is not reliably observable from the
  original tab after the fact. The implementation must avoid known new-window
  triggers by construction (root-only click + forbidden descendants) and the
  manual pilot must confirm no new windows open. `new_tab_not_supported` may
  remain as a defensive/manual diagnostic code, but the runtime contract must
  not depend on detecting it from the original tab.

Result: `{ "success": true, "url": "sha256:<list url hash>" }`.

### Op: `wait_job_detail_ready`

Bounded readiness check on the inline pane.

Instruction fields: `op`, `expected_title` (sanitized card title).

Rules:

- Poll `.job-detail-container .job-header-info` until its text contains
  `expected_title` (bounded timeout, userscript/backend constants).
- Timeout -> `success=false, error=detail_not_ready`.
- Never read `.job-boss-info`.

### Op: `read_jd` (new pane profile)

New selector profile `boss_recommended_pane_v1`, scoped to
`.job-detail-container .job-detail-box`:

- title/salary: `.job-detail-header .job-header-info`
  (salary via `[class*=salary]` inside it)
- description: `.job-detail-body p.desc`
- tags: `.job-detail-body ul.job-label-list li`
- location: `.job-detail-body .job-address`
- company: taken from the scan candidate (pane has none); the read result
  carries `company: null` and the service merges the candidate's company.
- Excluded forever: `.job-boss-info`, `.c-job-tools`, `.c-hot-link`,
  `.c-breadcrumb`, `.job-detail-op` (buttons).
- `source_kind: "boss_recommended_job"`; `page_url_hash` = hash of the
  current list URL (informational only, NOT the dedup key).

The existing `boss_recommended_job_v1` profile (standalone detail page)
remains unchanged for the single-job inspect path.

## Identity & Dedup

- The list URL never changes during a run, so `page_url_hash` is identical
  for every candidate and cannot dedup. Discovery therefore uses
  `external_id = job_key` for `JobPosting` upsert.
- `upsert_job_from_browser_jd` gains an optional `external_id_override`
  parameter; when present it replaces `page_url_hash` as the
  `platform="boss"` external id. Callers: discovery service passes
  `job_key`; existing inspect path passes nothing (behavior unchanged).
- `already_persisted` is detected after the inspect/upsert step, but
  `is_new_job=False` alone is NOT enough to skip. Existing
  `inspect_current_job` behavior still creates or reuses an
  `ApplicationRecord` for the current `resume_version_id` after an existing
  job is found. Discovery may skip only when the current job + current resume
  already has a reusable communicate action: either a non-terminal
  `boss_immediate_communicate` action awaiting approval/execution, or a
  terminal external result. This is `status=skipped`,
  `skip_reason=already_persisted`, `failure_code=null`.
- Implement the reusable-action check by extracting a public service/repository
  helper from the existing private communicate-action lookup patterns instead
  of duplicating ad hoc SQL. It should look up by `application_id`, `user_id`,
  and `action_type=boss_immediate_communicate`, and treat both non-terminal
  actions and terminal external results as reusable for discovery skip
  purposes.
- Cross-path duplicate note: single current-job inspect uses
  `external_id=page_url_hash`, while discovery uses `external_id=job_key`.
  The same BOSS job can therefore exist twice if a user first imports it via
  standalone detail inspect and later via discovery. v0.1 accepts this known
  tradeoff; unified BOSS job identity or title/company secondary merge is a
  future design decision.

## Backend Contracts

### Channel Schema

Extend bridge schemas:

- `op` values: `scan_visible_jobs`, `open_job_by_key`, `wait_job_detail_ready`.
- `InstructionOut` fields: `job_key`, `expected_list_url_hash`,
  `expected_title`, `max_items`, `selector_profile`.
- `ResultIn` fields: `job_candidates`.
- `InstructionResult` dataclass: `job_candidates: list[dict] | None`.

Sanitize `job_candidates` again in the bridge API before entering the
channel. Treat the userscript as helpful but not trusted: strip any key that
is not in the whitelist; drop entries containing raw hrefs/HTML/contact
names.

### API Endpoints

```text
POST /api/v1/boss/recommended-jobs/discovery
GET  /api/v1/boss/recommended-jobs/discovery/{run_id}
POST /api/v1/boss/recommended-jobs/discovery/{run_id}/pause   (crash-recovery only, v0.1)
POST /api/v1/boss/recommended-jobs/discovery/{run_id}/resume
```

Register static segments before any parameterized `/{...}` routes
(evolution-contracts §11).

Request:

```json
{ "resume_version_id": "uuid", "limit": 3, "mode": "prepare_only" }
```

- `limit` default 3, backend hard cap 10 (sync execution budget, below).
- v0.1 accepts only `prepare_only`. If `auto_execute` ever appears in the
  schema, the API layer must call
  `boss_dry_run_gate.assert_auto_execute_allowed()` and reject until the
  dry-run log gate passes; the service never implements auto-execute.

Response: same shape as batch-loop status (`run_id`, `status`, `total`,
`processed`, `items[]` with `job_key`, `rank`, `status`, `title`, `company`,
`job_id`, `application_id`, `decision`, `score`, `match_artifact_id`,
`action_id`, `failure_code`, `skip_reason`, `message`).

### Workflow Status

Run-level: `running`, `paused`, `completed`, `hard_stopped`, `failed`.

Item-level: `pending`, `opening`, `reading`, `persisted`, `matching`,
`prepared`, `needs_review`, `skipped`, `failed`, `stopped`.
(`pending` is the initial state; the PRD's earlier `discovered` name is
retired.) Processed = terminal states
(`prepared`, `needs_review`, `skipped`, `failed`, `stopped`).

`AgentRun.status` mapping mirrors `boss_batch_loop_service`:

- discovery `running` -> `AgentRun.status="running"`
- discovery `paused` -> `AgentRun.status="paused"`
- discovery `completed` -> `AgentRun.status="succeeded"` with
  `result["discovery_status"]="completed"`
- discovery `hard_stopped` -> `AgentRun.status="failed"` with
  `result["discovery_status"]="hard_stopped"`
- discovery `failed` -> `AgentRun.status="failed"` with
  `result["discovery_status"]="failed"`

## Service Design

New module: `backend/app/services/boss_recommended_discovery_service.py`.

Core flow:

```python
async def start_discovery_run(...):
    assert bridge connected
    assert no active batch-loop or discovery run for this user (else 409)
    create AgentRun(workflow_type="boss_recommended_discovery"); commit
    candidates = await scan_visible_jobs(limit); persist items; commit
    for each pending item:
        if hard_stop: mark remaining stopped; break
        open_job_by_key(job_key)          # pane switches in place
        wait_job_detail_ready(title)
        read_jd(pane profile)
        upsert(external_id=job_key, company from candidate)
        if existing action for this job+resume is reusable:
            mark skipped(already_persisted); continue
        run_boss_match_decision(...)
        if communicate: prepare_communicate_action(...); mark prepared
        elif needs_review: mark needs_review
        else: mark skipped
        commit after each transition
    mark completed
```

Reuse rules:

- Do not duplicate match prompt building or communicate preparation.
- Prefer extracting a shared helper so the inspect endpoint and discovery
  share `read_jd -> upsert` semantics without diverging.

Session/transaction shape: commit after run creation, after scan persist,
and after every item transition; roll back only the failing item.

### Concurrency & Locks

- One active discovery run per user; starting while a batch-loop run or
  another discovery run is `running` -> 409 `active_conflict`.
- The channel's single-application lock (`set_active_application`) is taken
  with a sentinel id `discovery:<run_id>` at run start and cleared on
  completion/hard-stop; this prevents interleaving with prepare/execute
  flows that use the same bridge.
- During a discovery run, do not call `channel.clear()` between item-level
  bridge ops, because `clear()` also clears `_active_application_id` and would
  release the sentinel lock. Either hold the sentinel until run completion or
  explicitly re-set `set_active_application("discovery:<run_id>")` before
  every bridge op. Final cleanup happens once at run completion, hard-stop,
  or unrecoverable failure.
- Serial item processing only (spec §14 user boundary & serialization).

### Execution Model (decision)

v0.1 mirrors the batch-loop: processing runs synchronously inside the POST
request. Consequences, accepted deliberately:

- The frontend result table is populated from the POST response (terminal
  states); live per-item progress is deferred to a future async version.
- `pause` in v0.1 is crash-recovery only (marks a stuck non-terminal run
  `paused` for later `resume`), matching spec §14 pause semantics. The UI
  hides the pause button in v0.1.
- Resume must rebuild the userscript's in-memory scan cache before trying to
  open any cached `job_key`: issue `scan_visible_jobs` again, match returned
  candidates by stable `job_key`, and then continue pending items. If the
  page cannot be re-scanned or pending keys are absent, mark affected
  non-terminal items `stopped` with `message="interrupted: rescan required"`
  rather than producing repeated `candidate_not_found` failures.
- Budget: limit default 3 keeps worst-case request time bounded
  (`RESULT_TIMEOUT_S=90` per bridge op is the ceiling; typical pane ops are
  ~1s; LLM match dominates). If a future async worker is introduced, pause
  becomes cooperative and must bind `agent_run_id` + queue namespace.

## Failure Matrix

| Condition | Item/run result | failure_code | Next action |
| --- | --- | --- | --- |
| Bridge disconnected before scan | run failed | `bridge_not_connected` | Open BOSS page and retry |
| Current page not the geek list | run failed | `not_recommended_list_page` | Navigate to recommend tab |
| Pane container missing | run failed | `pane_not_found` | BOSS layout changed; probe again |
| Empty scan on valid geek list | run completed | message `no_visible_jobs` | Scroll manually or change filters |
| `open_job_by_key` missing key | item failed | `candidate_not_found` | Rescan |
| List URL hash mismatch | hard stop | `page_mismatch` | User checks tab/page |
| Click caused navigation | hard stop | `unexpected_navigation` | Selector drift; probe again |
| Pane title not matching in time | item failed | `detail_not_ready` | Retry run |
| `read_jd` failed / JD too sparse | item failed | `read_failed` / `jd_too_sparse` | Manual inspect |
| Existing job+resume already has reusable communicate action/result | item skipped | skip_reason `already_persisted` | None (business outcome) |
| LLM/model failure | item failed | `match_failed` | Retry later |
| Communicate prepare 422 | item failed | `prepare_failed` | Review artifact/action |
| Captcha/rate limit marker | hard stop | `captcha_required` / `rate_limited` | Human resolves; no retry |
| 3 consecutive failed | run hard_stopped | `consecutive_failures` | Human review |

`needs_review`, `skipped` (including `already_persisted`) are not failures
and reset the consecutive-failure counter. Skip reasons are business metadata
(`skip_reason`), not `failure_code`.

## Frontend Design

Component: `frontend/src/features/applications/RecommendedDiscoveryPanel.tsx`,
next to `BatchModePanel`.

Controls:

- Bridge status tag via existing `useBridgeStatus`.
- Resume selector using the existing resume list/detail pattern.
- Limit stepper: default 3, max 10 (backend re-caps).
- Start button ("开始采集" / "开始巡航"; never "自动投递").
- Pause hidden in v0.1 (crash-recovery endpoint only).
- Result table: rank/title/company, status tag, decision/score, action link
  when `prepared`, failure message when `failed`.

State:

- POST returns the terminal run; GET `{run_id}` supports refresh recovery
  and the resume selector. Polling every 2-3s is only needed while a future
  async version exists; v0.1 may poll briefly to catch a crashed run.

UX copy: "采集与匹配" / "巡航"; no promise of automatic delivery.

## Privacy And Audit

- No raw URL persisted; hashes only (`sanitize_url` semantics).
- No raw HTML persisted. JD text persists only through ORM
  `JobPosting.jd_raw` (`EncryptedText`).
- `.job-boss-info` (recruiter name/title) is never read, returned, persisted,
  or logged. The pane profile whitelist excludes it explicitly.
- Candidate summaries are short, sanitized, stored in `AgentRun.result`
  (plaintext JSON column — accepted tradeoff: operational visibility for
  short sanitized fields; never put JD text or resume text there).
- Logs include `run_id`, `job_key`, `job_id`, `application_id`,
  `failure_code`; never full JD, resume text, cookies, tokens, raw URL, or
  contact names.
- Existing generated-artifact provenance and payload-hash approval boundary
  apply unchanged.

## Compatibility

- Single current-job inspect unchanged (`page_url_hash` dedup retained).
- Caller-supplied `job_ids` batch-loop unchanged.
- Communicate execute unchanged.
- Discovery-produced job ids remain eligible for the existing batch-loop.
- Cross-path identity duplication is accepted in v0.1: a standalone detail
  inspect row keyed by `page_url_hash` and a discovery row keyed by `job_key`
  may represent the same BOSS job. This is preferable to a weak title/company
  merge in the first release.

## Rollout

1. ~~Probe the real page~~ — done (`probe-evidence.md`).
2. Implement userscript ops + pane profile; extend
   `docs/boss-userscript-tests.html` for scan/open/wait/read on a fake pane.
3. Backend service/router/tests.
4. Frontend panel.
5. Real BOSS pilot: limit 3, prepare-only; confirm zero navigation and no
   accidental `.op-btn-chat` trigger; record into the pilot runbook.
6. Raise default limit toward 10 only after selector stability is observed.

## Open Decisions (non-blocking)

- Future async execution (queue worker) for live progress + cooperative pause.
- `scroll_next_batch` for beyond-viewport candidates.
- localStorage recovery of the active run id.
- Merging discovery and batch-loop UI into one tab.
- Unified BOSS job identity across standalone inspect and discovery paths.
