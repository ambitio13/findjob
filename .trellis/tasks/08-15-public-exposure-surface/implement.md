# 执行记录：公网暴露面收敛

## 改动清单

### R1: docs 在 prod 关闭
- `backend/app/main.py` `create_app()`: 按 `app_env == "prod"` 条件传 `docs_url=None` / `redoc_url=None` / `openapi_url=None`。
- 非 prod 环境保留 `/docs`、`/redoc`、`/openapi.json`。

### R2: userscript-bridge 路由条件挂载
- `backend/app/api/v1/router.py`: `if get_settings().app_env != "prod": api_router.include_router(userscript_bridge.router)`。
- `import userscript_bridge` 保持无条件（模块导入无副作用），仅挂载受控。
- prod 环境下 `/api/v1/userscript-bridge/*` 全部 404（路由不存在），是最强收敛。

### R3: compose 默认值翻转
- `docker-compose.yml`: `BOSS_USERSCRIPT_BRIDGE_ENABLED: ${BOSS_USERSCRIPT_BRIDGE_ENABLED:-1}` → `:-0`。
- `.env.example`: 新增 `BOSS_USERSCRIPT_BRIDGE_ENABLED=0` 文档与说明。

### R4: nginx body 限制
- `frontend/nginx.conf`: server 块添加 `client_max_body_size 12m;`。
- 与后端 `resume_max_size_mb=10` 对齐并留 2MB 余量。

### R5: 测试
- `backend/app/tests/test_public_exposure_surface.py`: 9 个测试。
  - prod app: `/docs`、`/redoc`、`/openapi.json` 404；`/userscript-bridge/status`、`/probe` 404。
  - test app (非 prod): `/docs` 200；`/userscript-bridge/status` 200。
  - compose: `BOSS_USERSCRIPT_BRIDGE_ENABLED` 默认 `:-0`。
  - nginx: `client_max_body_size` 存在。
- 全量测试 1030 passed，无回归。

## 兼容性
- local/test 行为完全不变（docs、bridge、上传全保留）。
- prod 开 bridge 需改代码（不可通过环境变量），这是刻意的架构级安全约束。

## 测试结果
```
9 passed, 1 warning in 0.35s  (test_public_exposure_surface.py)
1030 passed, 1 warning in 58.95s  (full suite)
```
