# Design: 油猴脚本桥接适配器

## 架构概览

```
┌─────────────────────────────────────────────────┐
│  前端 GuidedSubmitPanel                          │
│  ┌─────────────────────────────────┐            │
│  │ useBridgeStatus (3s 轮询)        │            │
│  │ GET /userscript-bridge/status    │            │
│  └─────────────────────────────────┘            │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  后端 API                                        │
│  ┌─────────────────────────────────────────┐    │
│  │ /userscript-bridge/                      │    │
│  │   status        (前端轮询)               │    │
│  │   next-instruction (油猴 long-poll)      │    │
│  │   result        (油猴 POST)              │    │
│  │   heartbeat     (油猴 POST)              │    │
│  └─────────────┬───────────────────────────┘    │
│                │                                 │
│  ┌─────────────▼───────────────────────────┐    │
│  │ UserscriptChannel (单进程内存队列)       │    │
│  │   - put_instruction()                    │    │
│  │   - take_instruction() (long-poll 5s)    │    │
│  │   - put_result()                         │    │
│  │   - take_result() (等待 + 超时)          │    │
│  │   - heartbeat / is_connected             │    │
│  └─────────────┬───────────────────────────┘    │
│                │                                 │
│  ┌─────────────▼───────────────────────────┐    │
│  │ UserscriptBossAdapter                    │    │
│  │   实现 PlatformAdapter 协议              │    │
│  │   UserscriptBossPage 实现 BossPage 接口  │    │
│  └─────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
                   │ HTTP (GM_xmlhttpRequest)
┌──────────────────▼──────────────────────────────┐
│  Tampermonkey 用户脚本 (页面内 JS 上下文)        │
│  @match https://www.zhipin.com/*                 │
│  - 5s 心跳                                       │
│  - 2s 轮询 next-instruction                      │
│  - 执行指令 (fill/click/check/count/read)        │
│  - POST result 回传                              │
└──────────────────────────────────────────────────┘
```

## 核心组件

### 1. UserscriptChannel (`userscript_channel.py`)

单进程内存队列。进程级单例（模块级 `_channel` 实例）。

```python
@dataclass
class Instruction:
    instruction_id: str        # uuid4
    op: str                    # "fill" | "click" | "check_visible" | "count" |
                               # "read_title" | "read_url" | "read_content"
    selector_kind: str | None  # "role" | "label" | "placeholder" | "css"
    selector_value: str | None
    selector_name: str | None
    fill_value: str | None

@dataclass
class InstructionResult:
    instruction_id: str
    success: bool
    visible: bool | None = None
    count: int | None = None
    text: str | None = None    # 已截断/脱敏
    url: str | None = None     # 仅 hash
    error: str | None = None   # 已脱敏

class UserscriptChannel:
    # 指令队列: asyncio.Queue
    # 结果存储: dict[instruction_id, InstructionResult]
    # 连接状态: last_heartbeat: datetime | None
    # 当前 application_id: str | None

    async def put_instruction(instruction) -> str
    async def take_instruction(timeout=5.0) -> Instruction | None  # long-poll
    async def put_result(result) -> None
    async def take_result(instruction_id, timeout=15.0) -> InstructionResult | None
    def heartbeat(url_hash, title) -> None
    def is_connected() -> bool  # last_heartbeat 在 15s 内
    def set_active_application(application_id) -> None
    def clear() -> None  # 重置所有状态
```

**安全约束**：
- 指令队列纯内存，`clear()` 在每次 prepare/submit 结束后调用
- `take_result` 超时返回 None（硬停止，不无限等待）
- `is_connected` 检查心跳在 15s 内（3 倍心跳间隔）

### 2. UserscriptBossPage (`userscript_adapter.py`)

实现 `BossPage` 同接口，每个方法转为指令通过 channel 发送。

```python
class UserscriptBossPage:
    """通过 UserscriptChannel 执行页面操作的 BossPage 实现。

    每个操作转为一条 Instruction 发给油猴脚本，等待 InstructionResult。
    UserscriptBossAdapter 在 prepare 阶段设置 _is_submit_phase=False，
    click() 在此阶段直接 raise（安全不变量：prepare 永不点击）。
    """

    def __init__(self, channel: UserscriptChannel, *, is_submit_phase: bool):
        self._channel = channel
        self._is_submit_phase = is_submit_phase

    # 定位器返回一个 "远端定位器"——携带 selector 信息
    def get_by_role(self, role, *, name=None) -> RemoteLocator
    def get_by_label(self, text) -> RemoteLocator
    def get_by_placeholder(self, text) -> RemoteLocator
    def locator(self, selector) -> RemoteLocator

    # 操作：将 RemoteLocator + 操作转为 Instruction
    async def fill(self, locator, value) -> None
    async def click(self, locator) -> None  # raise if not is_submit_phase
    async def is_visible(self, locator) -> bool
    async def count(self, locator) -> int
    async def text_content(self, locator) -> str | None

    # 诊断
    @property
    def url(self) -> str  # 通过 read_url 指令获取，返回 hash
    def url_hash(self) -> str  # 同上
    async def safe_title(self) -> str | None  # 通过 read_title 指令
    async def content_snippet(self, max_len=200) -> str  # 通过 read_content 指令
```

**RemoteLocator** 是一个简单 dataclass，携带 selector 信息：

```python
@dataclass
class RemoteLocator:
    selector_kind: str
    selector_value: str
    selector_name: str | None
```

### 3. UserscriptBossAdapter (`userscript_adapter.py`)

```python
class UserscriptBossAdapter:
    platform = "boss"

    async def prepare_submission(self, ctx: PrepareContext) -> PrepareResult:
        # 1. guard: channel.is_connected() == False → login_required
        # 2. channel.set_active_application(ctx.application_id)
        # 3. page = UserscriptBossPage(channel, is_submit_phase=False)
        # 4. classification = await classify_page(page)
        # 5. 非 form_ready → _prepare_failure
        # 6. _fill_and_snapshot (复用 adapter.py 中的逻辑)
        # 7. channel.clear()
        # 8. return PrepareResult

    async def submit_prepared(self, ctx: SubmitContext) -> SubmitResult:
        # 1. guard: channel.is_connected() == False → unknown
        # 2. channel.set_active_application(ctx.application_id)
        # 3. page = UserscriptBossPage(channel, is_submit_phase=True)
        # 4. classification = await classify_page(page)
        # 5. 非 form_ready → _submit_hard_stop
        # 6. re-apply message (fill 指令)
        # 7. click final submit (click 指令，恰好一次)
        # 8. classify_submit_result
        # 9. channel.clear()
        # 10. return SubmitResult
```

**复用策略**：`classifiers.py` 和 `selectors.py` 完全不修改。`_fill_and_snapshot` 逻辑
从 `adapter.py` 中提取为共享函数，或直接在 `userscript_adapter.py` 中复制相同逻辑
（因为 `UserscriptBossPage` 实现了相同的 `BossPage` 接口，`_resolve` 函数也能复用）。

### 4. HTTP 端点 (`api/v1/userscript_bridge.py`)

```python
router = APIRouter(prefix="/userscript-bridge", tags=["userscript-bridge"])

@router.get("/status", response_model=BridgeStatusResponse)
def bridge_status() -> BridgeStatusResponse

@router.get("/next-instruction")
async def next_instruction() -> Response  # 200 + InstructionOut 或 204

@router.post("/result", response_model=AckResponse)
async def submit_result(payload: ResultIn) -> AckResponse

@router.post("/heartbeat", response_model=AckResponse)
async def heartbeat(payload: HeartbeatIn) -> AckResponse
```

**无认证**：油猴脚本不带 `X-User-Id` header。安全性由以下保证：
- 指令队列只含操作指令（无 credentials）
- 回传结果只含脱敏值
- 指令的构造完全由后端控制（油猴脚本不能注入指令）

### 5. Tampermonkey 用户脚本 (`docs/boss-userscript.user.js`)

核心循环：
1. 每 5s 发送心跳（`POST /heartbeat`，携带 `page_url_hash` + 截断 `page_title`）
2. 每 2s 轮询指令（`GET /next-instruction`，long-poll）
3. 收到指令后执行：
   - `fill` → 解析 selector → 找到元素 → 填入值 → 回传 success
   - `click` → 解析 selector → 找到元素 → 点击 → 回传 success
   - `check_visible` → 解析 selector → 找到元素 → 回传 visible
   - `count` → 解析 selector → 回传 count
   - `read_title` → 回传 `document.title`（截断 120 字符）
   - `read_url` → 回传 `location.href`（后端只存 hash）
   - `read_content` → 回传 `document.body.innerText[:200]`
4. 回传结果（`POST /result`）

**选择器解析**（在油猴脚本中）：
```javascript
function resolveElement(selector) {
    if (selector.kind === 'css') {
        return document.querySelector(selector.value);
    }
    if (selector.kind === 'placeholder') {
        return document.querySelector(`[placeholder="${selector.value}"]`);
    }
    if (selector.kind === 'role') {
        // role=button name=发送 → button[aria-label="发送"] 或 文本匹配
        const elements = document.querySelectorAll(`[role="${selector.value}"]`);
        if (!selector.name) return elements[0];
        for (const el of elements) {
            if (el.textContent.includes(selector.name)) return el;
        }
    }
    if (selector.kind === 'label') {
        // label 文本匹配
        const labels = document.querySelectorAll('label');
        for (const label of labels) {
            if (label.textContent.includes(selector.value)) {
                const forId = label.getAttribute('for');
                if (forId) return document.getElementById(forId);
                return label.querySelector('input, textarea, select');
            }
        }
    }
    return null;
}
```

### 6. 前端 (`useBridgeStatus.ts` + GuidedSubmitPanel)

```typescript
// useBridgeStatus.ts — 3s 轮询 GET /userscript-bridge/status
export function useBridgeStatus() {
    const [connected, setConnected] = useState(false);
    // ... token-based cancellation (复用 useAgentRunPolling 模式)
    return { connected };
}

// GuidedSubmitPanel.tsx — 顶部状态指示
const { connected } = useBridgeStatus();
<Tag color={connected ? "green" : "default"}>
    {connected ? "🟢 油猴脚本已连接" : "⚪ 油猴脚本未连接"}
</Tag>
```

## 数据流

### Prepare 流程

```
1. 用户点击"开始引导投递"
2. 前端 POST /applications/{id}/platform-submissions/prepare (enqueu-and-poll)
3. 后端 worker 调用 adapter.prepare_submission(ctx)
4. UserscriptBossAdapter:
   a. 检查 channel.is_connected()
   b. 构造 UserscriptBossPage(is_submit_phase=False)
   c. classify_page(page):
      - 对每个 marker，发 check_visible/count 指令
      - 油猴脚本在页面内检查元素，回传结果
      - 分类器组装 PageClassification
   d. _fill_and_snapshot:
      - 发 fill 指令（油猴脚本填入消息）
      - 发 check_visible 指令（验证提交按钮可见）
      - 发 read_title / read_url 指令（组装快照）
      - 永不发 click 指令（is_submit_phase=False）
   e. 返回 PrepareResult(filled_preview, snapshot)
5. worker 持久化快照，创建审批 action
6. 前端轮询 run status，显示 filled_preview
```

### Submit 流程

```
1. 用户审批 → 点击"最终提交"
2. 前端 POST /applications/{id}/platform-submissions/{run_id}/submit
3. 后端 service 层检查幂等键 + assert_action_approved
4. 调用 adapter.submit_prepared(ctx)
5. UserscriptBossAdapter:
   a. 检查 channel.is_connected()
   b. 构造 UserscriptBossPage(is_submit_phase=True)
   c. classify_page → form_ready?
   d. 发 fill 指令（重新填入消息）
   e. 发 click 指令（恰好一次，点击"发送"按钮）
   f. classify_submit_result:
      - 发 count 指令（检查 success/duplicate/error markers）
      - 组装 SubmitResult
   g. 返回 SubmitResult(submitted/duplicate/unknown/platform_failure)
6. service 层持久化结果
```

## 边界与不变量

### 不改动的模块

| 模块 | 理由 |
|------|------|
| `base.py` | 协议不变 |
| `classifiers.py` | 依赖 BossPage 接口，UserscriptBossPage 实现同接口 |
| `selectors.py` | 选择器从后端指令下发 |
| `sanitizer.py` | 脱敏逻辑不变 |
| `platform_submission_service.py` | 只调 adapter 协议方法 |
| `queue/` | 不新增队列类型 |
| `runtime.py` (Playwright) | 保留为 fallback |
| `adapter.py` (RealBossAdapter) | 保留为 fallback |
| `FakeBossAdapter` | 不变 |

### 新增模块

| 模块 | 职责 |
|------|------|
| `userscript_channel.py` | 内存指令队列 |
| `userscript_adapter.py` | UserscriptBossAdapter + UserscriptBossPage + RemoteLocator |
| `schemas/userscript_bridge.py` | Pydantic 模型 |
| `api/v1/userscript_bridge.py` | 4 个 HTTP 端点 |
| `docs/boss-userscript.user.js` | Tampermonkey 用户脚本 |
| `features/applications/useBridgeStatus.ts` | 前端轮询 hook |

### 安全不变量对照

| 不变量 | 实现方式 |
|--------|----------|
| prepare 永不点击 | `UserscriptBossPage.click` 检查 `is_submit_phase`，False 时 raise |
| submit 恰好一次 click | `click_count` 断言 ≤ 1 |
| 不持久化 credentials | 指令队列纯内存，`clear()` 每次调用后清空 |
| 回传结果脱敏 | 只含 visible/count/title(截断)/url(hash)/error(脱敏) |
| 不自动导航 | 无 navigate 指令（read_url 只读） |
| 选择器隔离 | 选择器从后端 selectors.py 下发，油猴脚本不硬编码 |
| 审批边界 | service 层不变 |
| 幂等键 | service 层不变 |
| 跨用户 404 | bridge 端点无 application_id 参数 |
