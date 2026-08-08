# BOSS 链路可观测性与追踪硬化 Design

## Trace Model

推荐把 BOSS 半自动链路看成一条 trace：

`inspect` → `match` → `prepare` → `approval` → `bridge instruction` → `userscript result` → `execute outcome`

每段至少有一个稳定 ID，跨段优先使用 `agent_run_id` 和 `application_id`。页面侧使用
`page_id`、`expected_url_hash`、`instruction_id`，并只保存 hash 不保存原始 URL。

## Logging Contract

事件名保持领域化，例如：

- `boss.recommended_job.inspect_started`
- `boss.bridge.heartbeat_received`
- `boss.bridge.instruction_taken`
- `boss.bridge.instruction_requeued`
- `boss.bridge.result_received`
- `boss.communicate.execute_finished`
- `queue.workflow.missing_run`

字段允许缺失，但不能含义漂移。敏感文本使用 hash、长度、枚举 outcome 替代。

## Frontend Contract

失败 UI 不需要展示技术堆栈，但应保留：

- 用户可理解状态。
- 可复制 `agent_run_id`。
- 可复制或间接定位的错误分类。

## Compatibility

- 不引入外部 APM。
- 不改变 API 成功语义。
- 如需扩展错误 envelope，保持向后兼容字段。

## Risk

过度日志会让输出噪声增加。实现应优先覆盖状态转移点，不为每个内部 helper 打日志。
