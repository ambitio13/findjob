# 修复方案 v2：失败 run 可见性 + 执行轨迹 metadata/timing

（整合用户 7 条反馈）

## 问题根因

1. **失败 run 不可见**：`JobDetailPage` 只从 `listJobAnalyses`（查 `JobAnalysis` 表）取数据，但失败 run 按 design 不创建 `JobAnalysis`/`GeneratedArtifact`。`AgentRun` 表里虽然有失败 run，但**没有 `job_id` 列**（只在 `result` JSON 里），无法按 job 查询。`list_runs_for_user` 和 `GET /agent-runs` 都不支持 `job_id` 过滤。
2. **轨迹缺 metadata/timing**：后端 step 的 `result` JSON 已存了脱敏元数据（provider、model、latency_ms、prompt_version、message_count 等），但前端 Timeline 完全没渲染 `step.result`；schema 也没暴露 `created_at`，无法显示时间/耗时。

---

## 改动清单

### A. 数据模型：AgentRun 增加 `job_id` 列

**`backend/app/db/models/models.py`** — `AgentRun` 增加列（匹配 `GeneratedArtifact.job_id` 既有模式）：
```python
job_id: Mapped[str | None] = mapped_column(
    String(64), ForeignKey("job_postings.id"), nullable=True, index=True
)
```

**`backend/alembic/versions/0002_agent_run_job_id.py`** — 新迁移：
- 先读 `backend/alembic/versions/0001_initial.py` 确认 `revision = "0001_initial"`（已确认匹配），设 `down_revision = "0001_initial"`
- `upgrade()`: `op.add_column("agent_runs", ...)` + `op.create_index`
- `downgrade()`: `op.drop_index` + `op.drop_column`
- 不做 backfill（列 nullable，MVP 无存量数据）

### B. Repository 层：支持按 job_id + workflow_type 查 run

**`backend/app/db/repositories/agent_run_repo.py`**：
- `create_run` 增加 `job_id: str | None = None` 参数，传入 ORM 构造
- `list_runs_for_user` 增加 `job_id: str | None = None` 参数（已有 `workflow_type`），提供时 AND 到 filter

### C. Service 层：创建 run 时写入 job_id

**`backend/app/services/jd_analysis_service.py`**：
- `run_resume_aware_jd_analysis` 里 `create_run(...)` 调用增加 `job_id=job_id`（job_id 是函数参数，已在作用域内）
- 失败 run 也会带上 `job_id`（因为 `create_run` 在 `_fail_run` 之前调用）

### D. API 层：暴露 job_id/workflow_type 过滤 + created_at

**`backend/app/schemas/api.py`**：
- `AgentRunOut` 增加 `created_at: datetime | None = None`
- `AgentStepOut` 增加 `created_at: datetime | None = None`
- `AgentRunDetailOut` 增加 `created_at: datetime | None = None`
- `AgentRunDetailOut.steps` 改为 `Field(default_factory=list)`（反馈 #3）
- （`BaseSchema` 已设 `from_attributes=True`，声明字段后 `model_validate(orm)` 自动拾取）

**`backend/app/api/v1/agent_runs.py`**：
- `list_agent_runs` 增加 `job_id: str | None = Query(None)` 和 `workflow_type: str | None = Query(None)` 查询参数，传入 `list_runs_for_user`（反馈 #4）
- `get_agent_run_detail` 改用 `AgentRunDetailOut.model_validate(run)` 再赋 `steps`，自动拾取 `created_at`

### E. 前端类型 + API client

**`frontend/src/types/index.ts`**：
- `AgentRunOut` 增加 `created_at: string | null`
- `AgentStepOut` 增加 `created_at: string | null`
- `AgentRunDetailOut` 增加 `created_at: string | null`

**`frontend/src/api/client.ts`**：
- `listAgentRuns` 增加可选 `jobId?: string` 和 `workflowType?: string`，提供时传 `params: { job_id, workflow_type }`

### F. 前端 JobDetailPage：合并 runs + analyses，失败 run 可选可查

**`frontend/src/pages/jobs/JobDetailPage.tsx`**：

1. **数据获取**：`refreshData(selectNewest = false)` 并行调用 `listJobAnalyses(id)` + `listAgentRuns(1, 20, id, "resume_aware_jd_analysis")`，分别存 `analyses` 和 `runs`（反馈 #4：带 workflow_type 避免混入其他 agent run）
2. **合并视图**：定义本地类型（反馈 #5）：
   ```ts
   interface RunAnalysisView {
     run: AgentRunOut;
     detail: JobAnalysisDetailOut | null;
   }
   ```
   以 `runs` 为主表，按 `agent_run_id` 匹配挂载 `JobAnalysisDetailOut`；无匹配的是失败 run
3. **选择器**：下拉显示所有 run（成功 + 失败），带状态 Tag + 时间
4. **选中失败 run**：`AnalysisResult` 显示"未产出有效结果"提示（已有逻辑），`AgentProcessPanel` 显示失败轨迹
5. **handleRun 失败路径**：catch 块里 `await refreshData(true)` 强制选最新 run（即刚失败的，按 `created_at desc`），让用户立刻看到失败轨迹（反馈 #6：保证 catch 后能选中最新 failed run）
6. **handleRun 成功路径**：`setSelectedRunId(res.agent_run.id)` 后 `refreshData(false)`

### G. 前端 AgentProcessPanel：展示 metadata + timing

**`frontend/src/pages/jobs/JobDetailPage.tsx`** — `AgentProcessPanel`：

1. **Run 级 timing**：面板顶部显示 `started_at` → `finished_at` 及计算出的总耗时（用 `created_at` 辅助排序）
2. **Step 级**：每个 Timeline 节点下方渲染 `step.result` 的脱敏元数据（key-value 紧凑列表，过滤 null/undefined），并显示 `step.created_at` 时间戳
3. **call_model step**：特别高亮 `latency_ms`（如"模型耗时 123ms"）
4. 保持"不包含原始提示词或简历内容"的脱敏说明

### H. 后端测试

**`backend/app/tests/test_jd_analysis_api.py`**：
1. 新增 `_RaisingStubGateway`（`chat()` 抛异常），测试 model-call-failure 路径（step 3 失败 → 502 "model call failed" → 持久化 failed run + failed call_model step）
2. 新增测试（反馈 #7 锁定契约）：
   - 失败后 `GET /jobs/{job_id}/analyses` 仍为空
   - 但 `GET /agent-runs?job_id={job_id}&workflow_type=resume_aware_jd_analysis` 能返回 failed run
   - `GET /agent-runs/{run_id}/detail` 能看到 failed step 的 `result` metadata（provider/error_type）
3. 新增测试：失败 run 的 `job_id` 列正确关联到 job
4. 更新 `test_run_detail_returns_ordered_steps_user_scoped`：断言 `created_at` 非空

**`backend/app/tests/test_jd_analysis_contracts.py`**：
5. 更新 `test_agent_run_repo_create_update_step_list`：验证 `create_run` 接受 `job_id`，`list_runs_for_user` 的 `job_id` 过滤生效

### I. uv.lock 处理（反馈 #1：保持 .venv 方式）

**`.gitignore`** 增加 `uv.lock`。
质量门命令统一用 `.venv/bin/ruff` / `.venv/bin/pytest`，不用 `uv run`。
删除当前未跟踪的 `backend/uv.lock`。

---

## 不改动

- `AgentRun` 不加 per-step 精确计时（steps 只有 `created_at`，per-step `started_at`/`finished_at` 对 MVP 过重）
- 失败 run 仍不创建 `JobAnalysis`/`GeneratedArtifact`（design 正确行为，不变）
- `manual_jd_analysis_demo` 端点不变（demo run 无 job，`job_id=None` 天然成立）
- POST 失败响应结构不改（通过 `refreshData(true)` 按 `created_at desc` 选中最新 failed run）

## 质量门（统一用 .venv/bin，不用 uv run）

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && MODEL_PROVIDER=fake .venv/bin/pytest -q
cd frontend && pnpm lint && pnpm type-check && pnpm build
python3 .trellis/scripts/task.py validate .trellis/tasks/07-31-agent-run-observability-result-visibility
```
