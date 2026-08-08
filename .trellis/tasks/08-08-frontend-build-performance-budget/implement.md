# 前端构建性能债与 chunk 预算 Implementation Plan

## Checklist

- [ ] 读取 frontend `directory-structure.md`、`api-integration.md`、`css-layout.md`、
  `type-safety.md`、`quality.md` 和 shared `dependencies.md`。
- [ ] 运行 `pnpm build` 记录当前 chunk 输出。
- [ ] 检查 router/page 入口，识别可页面级懒加载的组件。
- [ ] 对 Applications/BOSS pilot 相关页面做 `React.lazy` 拆分。
- [ ] 增加稳定 loading fallback，确保操作型界面不出现突兀空白。
- [ ] 如有必要，新增 bundle 分析命令或文档化 `pnpm build` 输出判读。
- [ ] 运行前端质量门并记录 build chunk 变化。

## Validation

```bash
cd frontend
pnpm lint
pnpm type-check
pnpm build
```

必要时补充浏览器检查：

```bash
cd frontend
pnpm dev
```

## Rollback

逐个还原 route 懒加载改动即可；不涉及后端和数据库。
