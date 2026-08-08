# RealBossAdapter.execute_communication() CDP fallback Implementation Plan

## Checklist

- [x] 读取 backend `api-contracts.md`、`authentication.md`、`error-handling.md`、`logging.md`、
  `performance.md`、`quality.md`，shared `code-quality.md`。
- [x] 在 `backend/app/platforms/boss/adapter.py` 导入 communicate 相关 selector 和 classifier：
  `IMMEDIATE_COMMUNICATE_BUTTON`、`CONTINUE_COMMUNICATE_BUTTON`、`COMMUNICATION_MESSAGE_INPUT`、
  `COMMUNICATION_SEND_BUTTON`、`classify_communication_result`。
- [x] 实现 `RealBossAdapter.execute_communication(ctx)`，流程与 design.md 保持一致。
- [x] 使用 `sanitize_url(ctx.target_resource)` 或 `page.url_hash()` 做 page binding，不能记录原始 URL。
- [x] 统一结果映射：
  - duplicate marker → `CommunicationOutcome.duplicate`
  - succeeded marker → `CommunicationOutcome.succeeded`
  - platform failure / selector drift / fill/send failure → `CommunicationOutcome.failed`
  - unknown / runtime error / hash mismatch → `CommunicationOutcome.unknown`
- [x] 在异常路径写结构化日志，包含 `application_id`、sanitized target/hash、failure_code，不包含正文和 HTML。
- [x] 扩展 `backend/app/tests/test_boss_real_adapter.py` 或新增 focused tests，覆盖所有安全分支。
- [x] 确认 `backend/app/tests/test_platform_boss_adapter.py` 的 registry 优先级仍通过。
- [x] 更新 `docs/manual-boss-pilot.md` 的 CDP fallback 说明，明确 userscript 仍优先。

## Validation

```bash
cd backend
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/job_search_agent_test \
QUEUE_NAMESPACE=job-search-agent-test \
./.venv/bin/pytest app/tests/test_boss_real_adapter.py app/tests/test_platform_boss_adapter.py app/tests/test_boss_communicate_api.py -q
./.venv/bin/ruff check app
```

可选全量：

```bash
cd backend
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/job_search_agent_test \
QUEUE_NAMESPACE=job-search-agent-test \
./.venv/bin/pytest -q
```

## Review Gates

- CDP 模式不得新增任何 `page.goto`。
- 真实发送路径仍只能通过已批准 action 调起；adapter 内不绕过 service guard。
- 测试必须证明 wrong page / selector drift / unknown 不会点击发送。

## Rollback

只回滚 `RealBossAdapter.execute_communication()` 和相关测试/文档；不要改变 userscript 适配器。
