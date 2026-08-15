# 公网暴露面收敛：docs / userscript-bridge / nginx 请求体限制

## 依赖声明

- **Depends on**: 无（Phase 1）。与 `08-15-proxy-real-ip-trust-chain` 均改
  nginx.conf / compose，**建议在其后合入**以减少冲突（见父任务衔接声明）。
- **Blocks**: 无。

## Background（漏洞 B1 + B2 + B3）

审查发现三处公网暴露面问题，`08-11` R3（部署基线）未覆盖：

1. **B1 — API 文档开放**：`create_app()` 未设 `docs_url` / `openapi_url` /
   `redoc_url`，prod 下 `/docs`、`/redoc`、`/openapi.json` 全量公开，等于送出
   完整 API 地图（含 userscript-bridge 等敏感路由）。
2. **B2 — userscript-bridge 路由无条件挂载**：`api/v1/router.py` 无条件
   include `userscript_bridge.router`；`boss_userscript_bridge_enabled` 只控制
   adapter 选择，管不到 bridge HTTP 端点；compose 默认
   `BOSS_USERSCRIPT_BRIDGE_ENABLED=1`。后果：`GET /userscript-bridge/status`
   无鉴权泄露运行时元数据；数据面仅靠单一共享静态 channel token（嵌在
   userscript 配置块中，可被任何持有者消费指令/伪造 result）。PRD 明确"线上
   不做 userscript"，但没有任何一条要求 prod 不暴露这条数据面。
3. **B3 — nginx 缺 `client_max_body_size`**：默认 1MB，而后端允许 10MB 简历
   上传。上线后 >1MB 的简历在反代层直接 413，到不了后端的友好错误。

## Goal

prod 公网只暴露业务所需的 API 面；上传链路在反代层不截断。

## Requirements

- R1 `create_app()` 在 `app_env == "prod"` 时设置 `docs_url=None`、
  `redoc_url=None`、`openapi_url=None`（本地/test 保留，方便开发）。
- R2 userscript-bridge 路由按环境开关挂载：prod 下整个 router 不注册
  （比"端点内 503"更强的收敛），local/test 保持现状；开关可以是
  `app_env` 判定或独立配置项，implement 时定稿并写进 `.env.prod.example`。
- R3 compose 中 `BOSS_USERSCRIPT_BRIDGE_ENABLED` 默认值改为 `0`（本地开发需要
  时显式开启），并确认 prod 编排中不会误开。
- R4 `frontend/nginx.conf` 增加 `client_max_body_size`，与后端
  `resume_max_size_mb` 对齐（如 12m，留少量余量），加注释说明联动关系。
- R5 上述行为各有测试：prod 下 `/docs`、`/openapi.json`、
  `/userscript-bridge/status` 均 404；非 prod 下 `/docs` 仍可用。

## Acceptance Criteria

- [ ] AC1：`APP_ENV=prod` 的测试 app 上 `GET /docs`、`GET /openapi.json`、
      `GET /redoc` 返回 404。
- [ ] AC2：`APP_ENV=prod` 下 `GET /api/v1/userscript-bridge/status` 返回 404
      （路由未注册，而非 403/503）；local 下行为不变。
- [ ] AC3：compose 拉起后经 nginx 上传 12MB 文件，收到的是后端 413/422 响应
      （JSON body），而非 nginx 的 HTML 413 页面。
- [ ] AC4：`docker-compose.yml` 中 bridge 开关默认值为 0；`.env.example` 有
      对应说明。

## Constraints

- 不删除 userscript-bridge 代码与本地开发链路（08-03-boss-dry-run-gate 仍依赖）。
- nginx.conf 改动注意与 `08-15-proxy-real-ip-trust-chain` 的改动共存。
