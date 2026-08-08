# 前端构建性能债与 chunk 预算 Design

## Strategy

先测量，再拆分。目标不是追求极小 bundle，而是让首屏只加载应用框架和当前页面必需代码。

## Candidate Changes

- 使用 `React.lazy` / `Suspense` 对页面级 route 做动态导入。
- 将 Applications/BOSS pilot 相关页面从主 chunk 拆出。
- 评估 Ant Design Pro 重组件是否集中在少数页面，可通过页面懒加载自然拆出。
- 增加 bundle 分析脚本，可使用 Vite/Rollup 输出或轻量分析依赖；引入新依赖前需读 shared dependencies 规范。

## UX Contract

- 路由切换时需要有稳定 loading 状态，不能出现布局大跳动。
- 错误边界或 fallback 不应吞掉 API 错误。
- 移动端和桌面端导航行为保持不变。

## Compatibility

- 不替换 React Router。
- 不重构 Ant Design Pro 组件体系。
- 不把性能债和 BOSS 业务状态改动混在同一个 PR 里。

## Risk

动态导入可能暴露循环依赖或默认导出问题。实现时应保持每次拆分可单独 build 验证。
