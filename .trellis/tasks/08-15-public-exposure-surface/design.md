# Design：公网暴露面收敛（docs / bridge 路由 / nginx body 限制）

## 0. 边界

- **改动面**：`app/main.py`（docs 开关）、`app/api/v1/router.py`（bridge
  条件挂载）、`docker-compose.yml`（开关默认值）、`frontend/nginx.conf`
  （client_max_body_size）、`.env.example` 说明。
- **不改动**：userscript_bridge 端点实现与 channel token 规则（本地链路
  原样保留）、后端 `resume_max_size_mb` 校验逻辑。

## 1. docs 关闭（B1）

`create_app()` 按 `app_env` 条件传参（一次决策点，不散落）：

```python
prod = settings.app_env == "prod"
app = FastAPI(
    ...,
    docs_url=None if prod else "/docs",
    redoc_url=None if prod else "/redoc",
    openapi_url=None if prod else "/openapi.json",
)
```

否决"用独立配置项控制"：暴露面收敛是环境属性而非运维偏好，跟随
`app_env` 无第二真相源；想临时开文档属于调试场景，改环境变量重启即可。

## 2. bridge 路由条件挂载（B2）

`app/api/v1/router.py`：

```python
if get_settings().app_env != "prod":
    api_router.include_router(userscript_bridge.router)
```

- 选**路由不注册**而非端点内 403/503：已注册的路径仍会进入限流/日志/
  路由表，404 是最强的"不存在"；也顺带消灭 `/status` 这个唯一无鉴权端点。
- 选 `app_env` 判定而非新配置项：与 R3 的 compose 开关
  `BOSS_USERSCRIPT_BRIDGE_ENABLED`（控制 adapter）职责不同——后者是
  "本地要不要用真实链路"，前者是"prod 有没有这条数据面"，语义不可合并。
  双开关并存，`.env.example` 注释说明两者区别。
- `import userscript_bridge` 保持无条件（模块导入无副作用），仅挂载受控。
- worker 进程同样 import router（经 app.main？否——worker 只起 arq，
  不建 FastAPI app，不受影响；但 worker import `app.queue.worker` 链路
  不触达 api router，已核实无耦合风险，implement 时跑 worker 启动冒烟确认）。

### 现有测试兼容

bridge 相关测试（`test_userscript_*.py`）默认测试环境 `app_env=test`
→ 路由仍挂载，测试零改动。新增 prod 环境的 app 构建测试断言 404。

## 3. compose 默认值翻转（B2 配套）

`BOSS_USERSCRIPT_BRIDGE_ENABLED: ${BOSS_USERSCRIPT_BRIDGE_ENABLED:-1}`
改为 `:-0`。本地开发 runbook（`docs/boss-local-dev-runbook.md`）对应
加一行"需要 bridge 时 export =1"。注意：08-03-boss-dry-run-gate 任务
依赖本地 bridge，合入前与其负责人（同为本仓库）对齐 runbook 更新。

## 4. nginx client_max_body_size（B3）

```nginx
# 与后端 RESUME_MAX_SIZE_MB(默认 10)对齐并留余量；两者需同步调整。
client_max_body_size 12m;
```

放 `server` 块级。否决"精确 10m"：nginx 以此为硬截断，后端以字节精确
判断（10MB=10485760），留 2MB 余量避免边界值在反代层被截；真实超限仍由
后端 413/422 返回 JSON（AC3 断言响应体形状）。

## 5. 测试设计

- `test_docs_disabled_in_prod`：`APP_ENV=prod` 构建 app（monkeypatch
  settings + `create_app()` 重建）→ `/docs`、`/openapi.json`、`/redoc`
  404；`test` 环境下 `/docs` 200。
- `test_bridge_router_not_mounted_in_prod`：同上 app 断言
  `/api/v1/userscript-bridge/status` 404 且 app.routes 无该路径；
  非 prod 环境现有 bridge 测试全绿即覆盖。
- nginx/compose 侧：配置断言脚本（与 #2 的配置断言层合并，一个脚本查
  compose + nginx 两处），或退化为 runbook 手工勾选项——implement 时
  与 #2 统一，避免两套脚本。

## 6. 兼容性与回滚

- local/test 行为完全不变（docs、bridge、上传全保留）。
- prod 想临时调试 bridge：改 `APP_ENV` 不可取（影响鉴权/限流语义）——
  这是刻意的：prod 开 bridge 属于架构级决定，应走代码评审而非运维开关。
- 回滚：三处小改动各自独立可 revert。
