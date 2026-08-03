# API 层 failed/unknown 响应测试

## Goal

补充 `backend/app/tests/test_boss_communicate_api.py` 中 `failed` 和 `unknown` outcome
的 HTTP 响应测试。当前 API 层只测了 `submitted`（成功），未测 `failed`/`unknown` 的 HTTP
状态码和响应体。

## Confirmed Facts

- `test_boss_communicate_api.py` 已覆盖：execute approved → 200 (submitted)、unapproved →
  409、idempotent replay → 200、payload_hash_mismatch → 409、cross-user → 404。
- 缺口：`failed` outcome 的 API 响应测试、`unknown` outcome 的 API 响应测试。
- 服务层 `test_boss_communicate_service.py` 已覆盖 failed/unknown，但 API 层未覆盖。

## Requirements

### R1. failed outcome API 测试

- 测试当 `execute_communication` 返回 `CommunicationOutcome.failed` 时，API 响应的 HTTP
  状态码和响应体结构正确。
- 验证 `external_result_status == "failed"`。
- 验证 `failure_code` 字段存在且非空。

### R2. unknown outcome API 测试

- 测试当 `execute_communication` 返回 `CommunicationOutcome.unknown` 时，API 响应的 HTTP
  状态码和响应体结构正确。
- 验证 `external_result_status == "unknown"`。
- 验证 `failure_code` 字段存在且非空。

## Acceptance Criteria

- [ ] `test_boss_communicate_api.py` 新增 failed outcome 测试，通过
- [ ] `test_boss_communicate_api.py` 新增 unknown outcome 测试，通过
- [ ] 全部后端测试持续通过（758+）
- [ ] ruff clean

## Notes

- 参考 `test_boss_communicate_service.py` 中 failed/unknown 的测试模式。
- 使用 `FakeBossAdapter` 注入结果，不依赖真实 BOSS。
