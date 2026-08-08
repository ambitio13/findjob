# 前端构建性能债与 chunk 预算

## Goal

分析并收敛 frontend build 的大 chunk 警告，建立可执行的 bundle 预算和路由级拆分策略，
避免 BOSS pilot 面板、Ant Design Pro 和后续推荐列表功能继续扩大首屏包。

## Background

2026-08-08 对抗式审查中，`pnpm build` 通过，但 Vite 提示主 chunk 超过 500 kB：
`index-*.js` 约 2.23 MB minified，gzip 约 705 kB。当前 `frontend/package.json` 只有
`dev/build/preview/lint/type-check`，没有 bundle 分析脚本或预算门。

## Requirements

### R1. 明确首屏 bundle 现状

新增可重复的 bundle 分析方式，输出主要 chunk、依赖来源和 gzip 体积，避免只依赖 Vite warning。

### R2. 路由级拆分

优先按页面/功能拆分 React route，尤其是 BOSS pilot 相关页面和 Ant Design Pro 重依赖区域。
不为了数字漂亮牺牲代码可读性。

### R3. 预算门

建立初始预算：主入口 chunk 不再承载所有页面代码；若仍超过预算，必须在构建输出或文档中说明原因。

### R4. 体验不回退

拆包后路由加载、错误状态和空状态要保持可用；不能破坏现有应用页面导航。

## Acceptance Criteria

- [ ] `pnpm build` 不再出现当前主 chunk 级别的大包警告，或 warning 有明确豁免说明和后续拆分项。
- [ ] 至少 BOSS/Applications 相关重页面被懒加载或从主入口拆出。
- [ ] 新增 bundle 分析/预算命令或文档化检查方式。
- [ ] 前端 `pnpm lint`、`pnpm type-check`、`pnpm build` 通过。
- [ ] 后端质量门不受影响。

## Notes

- 此任务不改变 BOSS 业务 API 或后端行为。
