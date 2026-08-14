# Implement — 投递详情页拆分与匹配门控体验对齐

> 执行计划。纯前端改动。每阶段末尾有验证命令，通过后才进入下一阶段。
> 前置阅读：`prd.md`（起因与验收）、`design.md`（结构与取舍）。
> 顺序考量：A/B 是小范围契约修复，先做以尽快解除 dry-run 阻塞；C 是
> 大范围信息架构重构，最后做。

## Phase A：一键生成全部（PRD R2）

- [x] A1. `ArtifactChecklist.tsx`：提取 autoGenerate effect 主体为
      `generateAll` useCallback（串行 enqueueGeneration + pollRunIds 收集）。
- [x] A2. autoGenerate effect 改为调用 `generateAll`（行为不变，freshCreate
      语义保留）。
- [x] A3. 工具区新增「一键生成全部」按钮（Popconfirm；disabled 条件见
      design.md §2）；保留单类型入口。

验证：`cd frontend && pnpm lint && pnpm type-check && pnpm build` ✅

## Phase B：Pilot 门控对齐与开场白只读（PRD R3 + R4）

- [x] B1. `RecommendedJobPilotPanel.tsx`：PrepareApproveStepCard 渲染条件
      收窄为仅 `communicate`。
- [x] B2. needs_review 拦截块：Alert（error）+ score/risks/missing 展示 +
      「重新匹配」按钮 + 出路指引；删除 MatchStepCard 内旧 needs_review
      Alert 的"点击准备沟通继续"文案。
- [x] B3. handleMatch needs_review 分支文案改为拦截表述（semiAuto 自动停止
      逻辑保留；step 改为留在 match 而非推进到 prepare）。
- [x] B4. 开场白区域改为只读：Typography.Paragraph + copyable；移除
      `onOpeningMessageChange` prop 链；更新标题与提示文案；移除未再使用的
      `Input` import。
- [x] B5. 回归自查：communicate 决策路径的 match→prepare→approve 步骤渲染
      不受影响（PrepareApproveStepCard 条件仍含 communicate；开场白仅
      communicate 时展示）。

验证：`cd frontend && pnpm lint && pnpm type-check && pnpm build` ✅

## Phase C：投递详情页 Tabs 拆分（PRD R1）

- [x] C1. `ApplicationsPage.tsx` 的 `ApplicationDetail`：引入 AntD Tabs，
      按 design.md §1 分配公共头与各 Tab 面板归属。
- [x] C2. Tab 状态管理：useState + 切换 application 时重置默认 Tab；
      boss 平台条件渲染 Pilot Tab。
- [x] C3. 确认 Tabs 默认行为（切换不销毁已挂载面板），材料生成轮询跨 Tab
      不中断；显式声明 `destroyInactiveTabPane={false}`。
- [x] C4. 清理不再需要的包裹结构与冗余间距，检查各面板 props 传递不变。

验证：`cd frontend && pnpm lint && pnpm type-check && pnpm build` ✅

## Phase D：手动回归与验收（AC1–AC6）

- [ ] D1. 本机 dev 起前后端，按 design.md §6 手动回归清单逐项验证。
- [ ] D2. needs_review 场景用真实 BOSS 职位复现（用户 dry-run 现场即可）。
- [x] D3. 静态门 AC6 已通过（lint/type-check/build 全绿）；AC1–AC5 待手动
      回归。验证记录写入 check.jsonl。

## Review Gates

- Phase B 完成后：用户在真实 BOSS 页确认 needs_review 拦截体验与
  communicate 链路无回归，再继续 Phase C。
- Phase D 完成后：trellis-check 全量质量门 + 用户验收。

## 全局验证

```bash
cd frontend && pnpm lint && pnpm type-check && pnpm build
```

无后端改动：不跑 pytest（如需确认可选跑 `cd backend && pytest -q` 证明
未误伤）。
