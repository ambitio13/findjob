# Implement: 油猴脚本桥接适配器

## 执行顺序

### Step 1: `backend/app/platforms/boss/userscript_channel.py`

新建内存指令队列。

- `Instruction` dataclass: instruction_id, op, selector_kind/value/name, fill_value
- `InstructionResult` dataclass: instruction_id, success, visible, count, text, url, error
- `UserscriptChannel` class:
  - `asyncio.Queue` for instructions
  - `dict[instruction_id, InstructionResult]` for results
  - `last_heartbeat: datetime | None`
  - `active_application_id: str | None`
  - `put_instruction`, `take_instruction` (long-poll 5s)
  - `put_result`, `take_result` (timeout 15s)
  - `heartbeat`, `is_connected` (15s threshold)
  - `set_active_application`, `clear`
- 模块级单例 `get_channel() -> UserscriptChannel`
- 验证: `cd backend && .venv/bin/ruff check app/platforms/boss/userscript_channel.py`

### Step 2: `backend/app/platforms/boss/userscript_adapter.py`

新建适配器 + BossPage 实现。

- `RemoteLocator` dataclass: selector_kind, selector_value, selector_name
- `UserscriptBossPage`:
  - `__init__(channel, *, is_submit_phase)`
  - 实现 BossPage 全部方法（get_by_role/label/placeholder/locator/fill/click/is_visible/count/text_content/url/url_hash/safe_title/content_snippet）
  - `click` 在 `is_submit_phase=False` 时 raise `RuntimeError`
  - 每个方法构造 Instruction → `channel.put_instruction` → `channel.take_result` → 解析
- `UserscriptBossAdapter`:
  - `platform = "boss"`
  - `prepare_submission`: guard → channel.set_active_application → UserscriptBossPage(is_submit_phase=False) → classify_page → _fill_and_snapshot → clear → PrepareResult
  - `submit_prepared`: guard → channel.set_active_application → UserscriptBossPage(is_submit_phase=True) → classify_page → fill → click(once) → classify_submit_result → clear → SubmitResult
  - 复用 `classifiers.classify_page` / `classify_submit_result` / `selectors.*`
  - 复用 `adapter._resolve` 逻辑（复制或提取为共享函数）
- 验证: ruff check

### Step 3: `backend/app/schemas/userscript_bridge.py` + `backend/app/api/v1/userscript_bridge.py`

新建 schema + 4 个端点。

- `schemas/userscript_bridge.py`:
  - `BridgeStatusResponse`: connected, last_heartbeat, active_application_id
  - `InstructionOut`: instruction_id, op, selector_kind, selector_value, selector_name, fill_value
  - `ResultIn`: instruction_id, success, visible, count, text, url, error
  - `HeartbeatIn`: page_url_hash, page_title
  - `AckResponse`: ok
- `api/v1/userscript_bridge.py`:
  - `GET /status` → BridgeStatusResponse
  - `GET /next-instruction` → 200 + InstructionOut 或 204
  - `POST /result` → AckResponse
  - `POST /heartbeat` → AckResponse
- `api/v1/router.py`: 注册 `userscript_bridge.router`
- 验证: ruff check

### Step 4: `backend/app/core/config.py` + `backend/app/platforms/boss/registry.py`

- config.py: 新增 `boss_userscript_bridge_enabled: bool = False`
- registry.py: 三路选择
  ```python
  def get_adapter(*, scenario=None):
      if _userscript_enabled():
          from app.platforms.boss.userscript_adapter import UserscriptBossAdapter
          return UserscriptBossAdapter()
      if _flag_enabled():
          from app.platforms.boss.adapter import RealBossAdapter
          return RealBossAdapter()
      return FakeBossAdapter(scenario=scenario or "filled_preview")
  ```
- 验证: ruff check

### Step 5: `docs/boss-userscript.user.js`

Tampermonkey 用户脚本。

- `@match https://www.zhipin.com/*`
- `@grant GM_xmlhttpRequest`
- `@connect localhost, 127.0.0.1`
- 心跳循环 (5s)
- 轮询循环 (2s)
- 指令执行: fill/click/check_visible/count/read_title/read_url/read_content
- 选择器解析函数
- 结果回传
- 验证: 手动检查语法

### Step 6: 前端

- `frontend/src/api/client.ts`: 新增 `getBridgeStatus()`
- `frontend/src/features/applications/useBridgeStatus.ts`: 3s 轮询 hook
- `frontend/src/features/applications/GuidedSubmitPanel.tsx`: 顶部状态 Tag
- 验证: `cd frontend && pnpm lint && pnpm type-check`

### Step 7: 测试

- `backend/app/tests/test_userscript_channel.py`: 队列 put/take, 超时, 心跳, clear
- `backend/app/tests/test_userscript_boss_adapter.py`:
  - FakeUserscriptChannel (预编程指令-结果映射)
  - prepare: 未连接→login_required, 各分类结果, fill成功, fill失败, submit不可见, 永不click
  - submit: 未连接→unknown, 各分类结果, click恰好一次, click失败, classify结果
- `backend/app/tests/test_userscript_bridge_api.py`:
  - status 初始未连接
  - heartbeat → 已连接
  - next-instruction 空队列 204
  - result 回传
  - 心跳超时 → 未连接
- `backend/app/tests/test_platform_boss_adapter.py`:
  - 新增: userscript_enabled → UserscriptBossAdapter
- 验证: `cd backend && APP_ENV=test ... .venv/bin/pytest -q`

### Step 8: 文档

- 更新 `docs/manual-boss-pilot.md`: 新增油猴模式步骤
- 验证: 手动检查

### Step 9: 全量验证

```bash
cd backend && .venv/bin/ruff check .
cd backend && APP_ENV=test MODEL_PROVIDER=fake DATABASE_URL='postgresql+psycopg://app:app@localhost:5432/job_search_agent_test' .venv/bin/pytest -q
cd frontend && pnpm lint && pnpm type-check && pnpm build
python3 ./.trellis/scripts/task.py validate 08-03-userscript-bridge-adapter
git diff --check
```

## 回滚点

- 每个 Step 完成后可独立验证
- 如果 userscript 模式有问题，设置 `BOSS_USERSCRIPT_BRIDGE_ENABLED=false` 即可回退到 Playwright/CDP
- Playwright/CDP 代码保留不删除，作为 fallback
