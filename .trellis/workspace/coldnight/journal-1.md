# Journal - coldnight (Part 1)

> AI development session journal
> Started: 2026-07-31

---



## Session 1: 需求分析与全局规范收尾

**Date**: 2026-07-31
**Task**: 需求分析与全局规范收尾
**Branch**: `master`

### Summary

完成求职智能体 MVP 范围、Agent 分层方案、DeepSeek 模型网关、JD 手动录入和简历输出边界确认，并归档 bootstrap guidelines 任务。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `b4ebda1` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: 归档前置规划产物 (07-31-archive-previous-planning-artifacts)

**Date**: 2026-07-31
**Task**: Archive Previous Planning Artifacts
**Branch**: `master`

### Summary

在启动 v1 产品修复之前清理 Trellis 任务状态：归档三个已完成的前置任务，避免新的工作继承陈旧的活动任务指针。

### Main Changes

- 归档 `07-31-user-profile-preferences`（状态 completed，原 mvp-project-skeleton 子任务）。
- 归档 `07-31-resume-upload-parsing-foundation`（状态 completed，原 mvp-project-skeleton 子任务）。
- 归档 `07-31-mvp-project-skeleton`（父任务，状态 in_progress）。该父任务唯一的未完成子任务 `07-31-model-backed-jd-analysis-agent` 此前已归档，另两个子任务均已完成，因此判定父任务视为完成一并归档。
- 未修改任何产品代码（本任务为纯 Trellis 日常维护）。

### Git Commits

| Hash | Message |
|------|---------|
| `3724114` | chore(task): archive 07-31-user-profile-preferences |
| `63e6254` | chore(task): archive 07-31-resume-upload-parsing-foundation |
| `cec3a4b` | chore(task): archive 07-31-mvp-project-skeleton |

### Testing

- 归档后 `task.py list` 确认活动任务仅剩 v1-product-fixes 父任务及其 5 个子任务。
- `task.py current` 指针仍指向 `07-31-v1-product-fixes`，无陈旧指针残留。
- 工作区仅剩 `task.py start` 改写本子任务状态的 task.json 变更（待随本任务收尾提交）。

### Status

[OK] **Completed**

### Next Steps

- 提交本子任务 task.json 状态变更后归档本子任务。
- 进入子任务 #2 `07-31-agent-run-observability-result-visibility`（P0，前端结果可见性 + 运行步骤可观测）。


## Session 2: Model backed JD analysis agent

**Date**: 2026-07-31
**Task**: Model backed JD analysis agent
**Branch**: `feature/model-backed-jd-analysis-agent`

### Summary

Designed and implemented the resume-aware JD analysis agent through the model gateway, added backend contracts/executor/API persistence, integrated job detail UI, verified user-scoped agent run access, and completed quality gates.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `4ca4ee8` | (see git log) |
| `68ad2b0` | (see git log) |
| `4b26ffc` | (see git log) |
| `154cabb` | (see git log) |
| `91861a9` | (see git log) |
| `65ccc79` | (see git log) |
| `a93a4fb` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: Agent run observability result visibility

**Date**: 2026-07-31
**Task**: Agent run observability result visibility
**Branch**: `master`

### Summary

Completed the P0 agent run observability task: added job-scoped agent runs and migration, exposed run detail with ordered steps and timestamps, hydrated persisted JD analysis results in the job detail UI, made failed runs visible via job-scoped agent run reads, rendered sanitized metadata/timing, and passed backend/frontend/Trellis quality gates including Alembic upgrade.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `057a4dc` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 4: 简历上传自动解析 Profile

**Date**: 2026-08-01
**Task**: 简历上传自动解析 Profile
**Branch**: `master`

### Summary

实现简历上传后自动抽取结构化事实（contact/education/work_experience/projects/skills 等），带 AgentRun/AgentStep 审计链。上传路径内联抽取，模型失败仍返回 201 + _extraction.status=failed；重新解析端点失败返回 502。JD 分析升级 v2 prompt 消费结构化 facts。前端 ResumeDetailPage 渲染抽取状态、结构化事实卡片、Profile 草稿预览和重新解析按钮。后端 113 tests pass，ruff clean；前端 lint/type-check/build pass。补充 spec：logging.md AgentStep sanitization 不变量，ai-sdk-integration.md 上传内联抽取失败契约。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `98ebee3` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 5: JD paste auto-parsing workflow

**Date**: 2026-08-01
**Task**: JD paste auto-parsing workflow
**Branch**: `master`

### Summary

Implemented paste-first JD parsing: POST /api/v1/jobs/parse drives a 6-step AgentRun (jd_paste_parsing) via jd_parse_service + jd_paste_executor mirroring resume-fact extraction. JdParseResponse carries fields + extraction provenance so the frontend persists the durable jd_normalized = {_extraction, fields} contract on save. Parse failures recoverable (HTTP 200 + failed run + empty fields); blank raw_jd → 422. Step results sanitized. JobCreateModal two-phase paste→parse→edit→create; JobDetailPage renders jd_normalized.fields + _extraction. Fixed acceptance-rejected flat-structure deviation across backend/frontend/tests. 154 backend tests + frontend lint/type-check/build all green.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `824920a` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 6: Resume upload timeout resilience

**Date**: 2026-08-01
**Task**: Resume upload timeout resilience
**Branch**: `master`

### Summary

Accepted and archived the P0 resume upload timeout task. Upload now saves resumes before scheduling model-backed extraction, exposes extraction lifecycle state, normalizes timeout copy, and passed backend/frontend quality gates.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `25cab10` | (see git log) |
| `5115439` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 7: Async JD paste parsing (enqueue-and-poll)

**Date**: 2026-08-01
**Task**: Async JD paste parsing (enqueue-and-poll)
**Branch**: `master`

### Summary

Converted POST /api/v1/jobs/parse from a synchronous blocking model call to an enqueue-and-poll pattern. Backend: the endpoint now creates a durable 'queued' AgentRun, enqueues a jd_paste_parsing worker job via arq, and returns HTTP 202 immediately; the worker handler opens its own session, constructs its own ModelGateway, re-checks ownership, and stores sanitized fields+extraction in AgentRun.result (never raw JD text). Frontend: JobCreateModal rewritten to submit→poll→hydrate→retry, mirroring ResumeDetailPage with recursive setTimeout (3s) + active flag + TERMINAL_JD_PARSE_STATUSES. Added JdParseSubmitResponse type and TERMINAL_JD_PARSE_STATUSES constant. Tests: two-layer structure (API HTTP-202 contract with patched get_queue + worker handler execution with stub gateways), covering enqueue success/failure, ownership scoping, blank-JD 422, handler success/failure/owner-mismatch/missing-run, and raw-JD sanitization across all persisted rows. Fixed ruff import-sort/unused-import issues and ESLint prefer-const on the polling active flag.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `2dad958` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 8: Async resume fact extraction (enqueue-and-poll)

**Date**: 2026-08-01
**Task**: Async resume fact extraction (enqueue-and-poll)
**Branch**: `master`

### Summary

Migrated resume fact extraction from FastAPI BackgroundTasks (upload) and synchronous blocking (re-extract) to the shared Redis-arq worker queue. Both endpoints now create a durable queued AgentRun before enqueue and return immediately; the worker executes model work in a separate process. Added ResumeFactExtractionPayload (references resume_id/version_id, no raw text crosses queue boundary), split extract_resume_facts into extract_resume_facts_with_run mirroring the JD parse pattern, registered the resume_fact_extraction worker handler, and added enqueue-failure → failed-run flip. Sanitization preserved: only counts/lengths in run/step metadata. Frontend re-extract copy updated to reflect async behavior; existing polling logic already handles pending→running→succeeded/failed. New test_resume_extraction_api.py (API+worker two-layer) and rewritten test_resume_upload.py extraction tests use patched get_queue so no real Redis is required.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `a4ec9bb` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 10: 异步 Agent Run 前端 UX 与可观测性统一

**Date**: 2026-08-01
**Task**: Async Job UX and Observability
**Branch**: `master`

### Summary

完成异步队列架构升级父任务的最后一个子任务：统一前端异步 agent-run 体验。将 JobCreateModal、JobDetailPage、ResumeDetailPage 三个组件中各自重复实现的递归 setTimeout + token/ref 轮询模式，收敛到共享 `useAgentRunPolling` hook；将散落的状态常量、标签/颜色映射集中到 `@/features/agent-runs/status.ts`；新增 `AgentRunStatusTag` 组件和 `copy.ts` 文案模块，确保异步进度/成功/失败措辞跨工作流一致。ResumeDetailPage 因轮询不同端点（getResume 而非 getAgentRunDetail）保留其原有轮询逻辑，仅统一错误文案。前端 lint/type-check/build 全绿，后端 195 tests pass，task.py validate 通过。同步更新 frontend spec（api-integration、hooks、components）记录新的共享轮询/状态/展示约定。归档子任务及父任务，活动任务清零。

### Main Changes

- 新增 `frontend/src/features/agent-runs/status.ts`：`AgentRunStatus` 联合类型、`TERMINAL_AGENT_RUN_STATUSES`/`ACTIVE_AGENT_RUN_STATUSES` 集合、`AGENT_RUN_STATUS_LABEL`/`AGENT_RUN_STATUS_COLOR` 映射、`normalizeAgentRunStatus()`、`AGENT_RUN_POLL_INTERVAL_MS`；保留 deprecated `TERMINAL_JD_PARSE_STATUSES` 向后兼容。
- 新增 `AgentRunStatusTag.tsx`：共享状态标签组件。
- 新增 `useAgentRunPolling.ts`：共享轮询 hook，ref 管理回调避免定时器重建，token 取消，卸载清理。
- 新增 `copy.ts`：`asyncRunProgressMessage`/`asyncRunSuccessMessage`/`asyncRunFailureMessage`/`asyncRetryLabel`/`runIdHint`，均接受 `workflowLabel` 参数。
- 重写 `JobCreateModal.tsx`：移除手写轮询（pollTimerRef/pollTokenRef/stopPolling/pollRunDetail/JD_PARSE_POLL_MS），改用 `useAgentRunPolling` + `pollRunId` state。
- 重写 `JobDetailPage.tsx`：同上改用共享 hook，`message.useMessage()` + contextHolder 替代模块级 message。
- `ResumeDetailPage.tsx`：引入 `asyncRunFailureMessage("抽取")` 统一重新解析失败文案。
- `types/index.ts`：为 `TERMINAL_JD_PARSE_STATUSES` 添加 `@deprecated` JSDoc。
- 更新 `.trellis/spec/frontend/api-integration.md`：记录共享轮询 hook、status 模块、展示映射、文案模块、`as AgentRunStatus` 类型桥接约定。
- 更新 `.trellis/spec/frontend/hooks.md`：状态值改为 queued/running/succeeded/failed/not_run，指向共享 hook。
- 更新 `.trellis/spec/frontend/components.md`：指向 `AgentRunStatusTag` 复用。

### Git Commits

| Hash | Message |
|------|---------|
| `3248b59` | feat(frontend): unify async agent-run polling & status UX |
| `d8636ab` | chore(task): archive 08-01-async-job-ux-observability |
| `ed239d1` | chore(task): archive 08-01-async-queue-architecture-upgrade |

### Testing

- 前端 `pnpm lint` ✓（无错误）
- 前端 `pnpm type-check` ✓（无错误）
- 前端 `pnpm build` ✓（3.05s，4130 modules transformed）
- 后端 `.venv/bin/pytest -q` ✓（195 passed, 1 warning）
- `python3 .trellis/scripts/task.py validate 08-01-async-job-ux-observability` ✓
- `git diff --check` ✓（无空白错误）

### Status

[OK] **Completed**

### Next Steps

- None - 父任务 `08-01-async-queue-architecture-upgrade` 5/5 子任务全部完成并归档，活动任务清零。


## Session 9: 异步 JD 分析工作流入队迁移

**Date**: 2026-08-01
**Task**: 异步 JD 分析工作流入队迁移
**Branch**: `master`

### Summary

将简历感知 JD 分析从同步请求等待迁移为异步队列执行（enqueue-and-poll）。后端新增 ResumeAwareJdAnalysisPayload、run_resume_aware_jd_analysis_worker、resume_aware_jd_analysis handler；路由改为 202 + 入队立即返回，前置校验 404/422、重复活跃 run 409 守卫、Redis 故障翻转 failed。前端 JobDetailPage 轮询 getAgentRunDetail + hasActiveRun 禁用按钮。测试全面重写为异步模式（21 项全绿）。质量门全部通过。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `3fba4ae` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 10: Prompt template extraction

**Date**: 2026-08-01
**Task**: Prompt template extraction
**Branch**: `wanzhen`

### Summary

Moved JD analysis, JD paste parsing, and resume fact extraction system prompts into editable Markdown templates; added loader/tests, package data wiring, and backend prompt-template spec guidance. Verified backend ruff and pytest with an isolated test database.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `7e063c6` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 11: Application state machine and failure envelope

**Date**: 2026-08-01
**Task**: Application state machine and failure envelope
**Branch**: `wanzhen`

### Summary

Implemented the reliability foundation for the application-readiness loop: ApplicationStatus StrEnum with a full transition table, ApplicationFailureEnvelope (safe, metadata-only, strips over-long source IDs), ApplicationSourceSnapshot with a deterministic sha256 hash over job/resume/profile/prompt-version metadata (never raw text), ActiveOperationKey for duplicate-run detection, and a timeline event model. Added 135 unit tests covering all 6 acceptance criteria (transition validity, failure-envelope validation, source-hash change detection, no-raw-text-leak, dedup contract). All 333 backend tests pass; ruff clean.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `3205d2b` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 12: Application Records Center

**Date**: 2026-08-01
**Task**: Application Records Center
**Branch**: `wanzhen`

### Summary

Implemented the application records center, replacing the placeholder route with full CRUD + state machine. Extended ApplicationRecord with user_id/latest_agent_run_id/latest_error/readiness_snapshot (migration 0003), added application_repo + application_service, and implemented GET/POST /applications, GET /applications/{id}, PATCH /applications/{id}/status, POST /applications/{id}/timeline. Enforces transition table (422), cross-user 404, duplicate-create returns existing record, and transactional status+timeline. 19 tests added, all passing, ruff clean.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `56dcfc7` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete
