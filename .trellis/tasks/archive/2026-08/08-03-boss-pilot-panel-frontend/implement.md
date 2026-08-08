# 前端 RecommendedJobPilotPanel 组件 Implementation Plan

## Current State

组件主体已经存在，且当前工作区还有未提交的前端在途改动：

- `frontend/src/features/applications/RecommendedJobPilotPanel.tsx`
- `frontend/src/pages/applications/ApplicationsPage.tsx`

后续智能体接手时必须先审阅这些 diff，再决定是收敛、补测还是拆分提交。不要从零重写组件。

## Checklist

- [x] 读取 frontend `components.md`、`api-integration.md`、`state-management.md`、
  `css-layout.md`、`type-safety.md`、`quality.md` 和 shared `typescript.md`。
- [x] 审阅当前未提交 diff，确认它属于本任务：
  - `needs_review` 可人工继续 prepare。
  - `BossInspectEntry` 打破“必须先有 application 才能读取当前职位”的依赖。
- [x] 补齐 `BossInspectEntry` 的类型、错误状态、空简历状态和 bridge 未连接状态。
- [x] 确认 `RecommendedJobPilotPanel` 在 `communicate` 和 `needs_review` 下的按钮门控正确：
  - `skip` 不能继续 prepare。
  - `needs_review` 必须人工点击后才能继续。
  - approve 和 execute 始终人工触发。
- [x] 确认 semi-auto 只自动执行 inspect → match → prepare，不自动 approve/execute。
- [x] 确认切换 application、重新开始、API 失败时不会复用旧 action/result。
- [x] 确认失败状态展示 `agent_run_id` 或可定位错误信息，不暴露 HR 消息之外的敏感内容。
- [x] 根据风险补前端测试；如果当前项目没有前端测试框架，至少用 `pnpm lint/type-check/build`
  和浏览器手动检查记录替代。
- [x] 根据风险补前端测试；如果当前项目没有前端测试框架，至少用 `pnpm lint/type-check/build`
  和浏览器手动检查记录替代。
- [x] 提交时只包含本任务前端文件和必要任务文件，不混入其它横切任务。

## Validation

```bash
cd frontend
pnpm lint       # PASS (eslint . no output)
pnpm type-check # PASS (tsc -b --noEmit no output)
pnpm build      # PASS (built in 3.62s; existing 2.2MB chunk warning tracked in 08-08-frontend-build-performance-budget)
```

如果后端/Compose 可用，补充手工检查：

```bash
docker compose up -d --build
./scripts/e2e-smoke.sh --skip-up
```

## Review Gates

- 真实发送消息前必须仍有人工 approve + execute 两道显式动作。
- `needs_review` 只能进入人工继续路径，不能被 semi-auto 自动推进到 execute。
- 无 application 的 BOSS 读取入口成功后，必须选择新创建的 application 并刷新详情。
- 前端 build 允许保留既有 chunk warning，但要在性能债任务中继续跟踪。

## Rollback

如出现回归，优先还原 `RecommendedJobPilotPanel.tsx` 与 `ApplicationsPage.tsx` 的本任务改动；
不要回滚已归档的 userscript bridge、selector dispatch、selector drift 后端修复。
