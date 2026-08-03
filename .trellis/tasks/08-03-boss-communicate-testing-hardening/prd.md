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

以下 9 个子任务，按路线图优先级排列：

| 子任务 | 路线图编号 | 优先级 | 类型 |
|--------|-----------|--------|------|
| 真实 BOSS 页面验证（阶段 1-7） | P0-2 | P0 | 手动验证 |
| JS 测试页（J1-J12） | P1-4 | P1 | 代码 |
| API 层 failed/unknown 响应测试 | P2-1 | P2 | 代码 |
| userscript 选择器改为后端下发 | P2-2 | P2 | 代码 |
| 选择器漂移自动检测 | P2-4 | P2 | 代码 |
| 前端 RecommendedJobPilotPanel 组件 | P3-1 | P3 | 代码 |
| 推荐列表大循环 | P3-2 | P3 | 代码 |
| RealBossAdapter.execute_communication() CDP fallback | P3-3 | P3 | 代码 |
| 10 次 dry-run 门槛验证 | P3-4 | P3 | 手动验证 |

### Out of Scope

- 已完成的 B1/B2/B3 修复不在本任务范围。
- 新增平台适配（非 BOSS）不在本任务范围。

## 建议执行顺序（非强制依赖）

```
P1-4 (JS 测试页)     ────────────────────────┐
P2-1 (API 测试)      ── 可独立先行            │
                                             │
P0-2 (真实页面验证)   ── 可与代码任务并行       │
  │                                          │
  ├── P3-1 (前端 PilotPanel) ── 依赖 P0-2 通过 │
  │     │                                    │
  │     └── P3-2 (推荐列表大循环)              │
  │                                          │
  └── P3-4 (dry-run 门槛) ── 依赖 P0-2        │
                                             │
P2-2 (选择器后端下发) ──┐                     │
  └── P2-4 (漂移检测)   ── 受益于 P2-2        │
                                             │
P3-3 (CDP fallback)   ────────────────────────┘
```

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

- [ ] 9 个子任务全部创建并填写 PRD
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
