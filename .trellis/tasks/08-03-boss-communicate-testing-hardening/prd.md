# BOSS 自动沟通测试与硬化

## Goal

系统性完成 BOSS 自动沟通功能的测试覆盖、健壮性增强和功能扩展，达到生产验收标准。

本任务是父任务，覆盖 `docs/boss-communicate-testing-plan.md` 第 4 节路线图中全部 9 个未完成
工作项（P0-2 到 P3-4）。每个工作项建模为一个子任务，独立 PRD，独立验收。

## Confirmed Facts

- B1/B2/B3 三个已知代码问题已修复（commit `782bf09`），758 测试通过，ruff clean。
- `docs/boss-communicate-testing-plan.md` 是本任务的主文档，包含完整的测试覆盖分析、
  真实页面验证步骤（阶段 1-7）和后续路线图。
- 设计文档位于
  `.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`。

## Scope

### In Scope

以下 9 个路线图子任务，按路线图优先级排列：

| 子任务 | 路线图编号 | 优先级 | 类型 |
|--------|-----------|--------|------|
| 真实 BOSS 页面验证（阶段 1-7） | P0-2 | P0 | 手动验证 |
| JS 测试页（J1-J12） | P1-4 | P1 | 代码 |
| API 层 failed/unknown 响应测试 | P2-1 | P2 | 代码 |
| userscript 选择器改为后端下发（已归档） | P2-2 | P2 | 代码 |
| 选择器漂移自动检测（已归档） | P2-4 | P2 | 代码 |
| 前端 RecommendedJobPilotPanel 组件 | P3-1 | P3 | 代码 |
| 推荐列表大循环 | P3-2 | P3 | 代码 |
| RealBossAdapter.execute_communication() CDP fallback | P3-3 | P3 | 代码 |
| 10 次 dry-run 门槛验证 | P3-4 | P3 | 手动验证 |

另有 4 个横切质量门/工程债子任务：

| 子任务 | 优先级 | 类型 |
|--------|--------|------|
| BOSS E2E smoke 与测试隔离质量门 | P1 | 测试/工具 |
| BOSS 链路可观测性与追踪硬化 | P1 | 可观测性 |
| BOSS 本地开发体验与运行手册硬化 | P2 | 开发体验 |
| 前端构建性能债与 chunk 预算 | P2 | 性能 |

### Out of Scope

- 已完成的 B1/B2/B3 修复不在本任务范围。
- 新增平台适配（非 BOSS）不在本任务范围。

## 建议执行顺序（阶段 A→E 路线）

```
阶段 A (可用): 前端 PilotPanel + 半自动 loop
  └── P3-1 (前端 PilotPanel) — inspect→match→prepare 串行自动，execute 人工确认
        │   后端 API 全部就绪，无需后端改动
        │   半自动 loop 为阶段 C 提供 dry-run 积累工具
        │
阶段 B (可靠): 选择器后端下发 + 漂移检测
  ├── P2-2 (选择器后端下发) — 已归档，Instruction 新增 extra_selectors 字段
  └── P2-4 (漂移检测) — 已归档，execute_communication Step 3.5 提前检测 selector_drift
        │
阶段 C (积累): Dry-run 门槛积累
  └── P3-4 (dry-run 门槛) — 用半自动 loop 积累 10 次无事故，至少 2 次 duplicate
        │   硬前置：阶段 A 完成（半自动 loop 面板可用）
        │
阶段 D (规模): 推荐列表全自动大循环
  └── P3-2 (推荐列表大循环) — auto-execute 开关默认关闭，门槛通过后启用
        │   硬前置：阶段 C 通过（10 次 dry-run 无事故）
        │
阶段 E (兜底): CDP fallback
  └── P3-3 (CDP fallback) — 可在任何阶段独立实施，不阻塞主线

阶段 F (最终真实链路验收): 全部修复完成后由 Codex 执行
  └── 真实 BOSS 页面端到端验证 — 放在最后，不提前扩大自动沟通外部副作用

并行任务（不阻塞主线）:
  ├── P0-2 (真实页面验证) — 阶段 1-7 手动验证
  ├── P1-4 (JS 测试页) — J1-J12
  └── P2-1 (API 测试) — failed/unknown 响应测试
```

**核心思路**: 半自动 loop（阶段 A）是整条路线的主线工具——它让用户从 curl 操作
解放出来，为阶段 C 积累 dry-run 数据，为阶段 D 的 auto-execute 提供渐进式信任基础。


## Requirements

### R1. 子任务完整覆盖

每个路线图工作项必须有对应的子任务，子任务 PRD 包含明确的 Goal、Requirements 和
Acceptance Criteria，引用 `docs/boss-communicate-testing-plan.md` 对应章节。

### R2. 手动验证任务可跟踪

P0-2（真实页面验证）和 P3-4（dry-run 门槛）是手动验证类任务，其 PRD 必须包含可勾选的
验证清单，使进度可跟踪。

### R3. 父任务验收对照

父任务验收时对照 `docs/boss-communicate-testing-plan.md` 第 5 节验收标准汇总中的
验收通过条件 1-8。

## Acceptance Criteria

- [ ] 9 个路线图子任务全部创建并填写 PRD
- [ ] 横切质量门子任务完成：测试 DB/queue 隔离 + 一键 E2E smoke 可重复运行
- [ ] 横切可观测性任务完成：BOSS/bridge/queue 日志可用统一 ID 串联
- [ ] 横切开发体验任务完成：本地开发、排障、清理 runbook 和脚本入口可用
- [ ] 横切性能债任务完成：frontend build 大 chunk 有拆分或明确预算说明
- [ ] 子任务 1（P0-2）：阶段 1-7 验证通过，至少 3 次 `succeeded` + 1 次 `duplicate_detected`
- [ ] 子任务 2（P1-4）：JS 测试页 J1-J12 全部通过
- [ ] 子任务 3（P2-1）：API 层 failed/unknown 响应测试通过
- [ ] 子任务 4（P2-2）：userscript 不再硬编码选择器
- [ ] 子任务 5（P2-4）：选择器漂移检测在 prepare 阶段生效
- [ ] 子任务 6（P3-1）：前端 PilotPanel 组件可显示状态并 approve/execute
- [ ] 子任务 7（P3-2）：推荐列表可批量处理
- [ ] 子任务 8（P3-3）：CDP fallback 可执行 communicate
- [ ] 子任务 9（P3-4）：10 次 dry-run 无事故记录已归档
- [ ] 所有后端测试持续通过（758+）
- [ ] ruff / 前端 lint 全部 clean
- [ ] 验证记录已归档到 `check.jsonl` 或开发日志

## Notes

- 主文档：`docs/boss-communicate-testing-plan.md`
- 设计文档：`.trellis/tasks/archive/2026-08/08-03-boss-jd-read-auto-communicate-agent/design.md`
- 父子结构不是依赖系统；若子任务间有顺序，顺序写在上面的「建议执行顺序」中。
