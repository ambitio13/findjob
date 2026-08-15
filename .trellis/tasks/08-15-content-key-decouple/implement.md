# Implement：内容加密密钥与认证签名密钥解耦

## 改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/core/content_crypto.py` | 双前缀 + 双密钥：`_fernet_v1()`（enc1$ 读兼容）、`_fernet_v2()`（enc2$ 新写）；`encrypt_text` 输出 `enc2$`，`decrypt_text` 按前缀分派 |
| `backend/app/core/config.py` | 新增 `content_encryption_key: str = ""` |
| `.env.example` | 新增 `CONTENT_ENCRYPTION_KEY=` 文档（生成命令 + 备份警告） |
| `backend/app/tests/test_content_crypto.py` | 16 个测试覆盖双前缀往返、enc1$ 读兼容、混存、prod fail-fast、非 prod 回退、错误 key 塌缩、ORM/API 契约 |

## AC 对照

- **AC1（enc1$ 读兼容）**：`test_enc1_legacy_row_decryptable` — 用旧派生密钥构造 enc1$ token，新代码正确解密。
- **AC2（enc2$ 新写 + 混存）**：`test_encrypt_decrypt_roundtrip` 断言新写产 `enc2$`；`test_enc1_and_enc2_coexist` 断言混存各自正确解密。
- **AC3（prod 缺 key fail-fast）**：`test_prod_missing_content_key_raises` + `test_prod_missing_content_key_raises_on_decrypt_v2` — prod 环境缺 key 时 encrypt/decrypt 均抛 `RuntimeError`（信息含变量名）。
- **既有行为不变**：legacy 明文/空串/解密失败塌缩为空串均有测试覆盖；`test_enc2_wrong_key_collapses_to_empty` 验证错误 key 不泄露密文。

## 关键设计决策落实

1. **不做一次性迁移**：enc1$ 存量行原样保留，读取时按前缀选 v1 派生密钥解。
2. **新密钥直接作 Fernet key**：`CONTENT_ENCRYPTION_KEY` 必须是 Fernet 规范密钥（`Fernet.generate_key()` 生成），格式错自然抛异常。
3. **校验位置在 `_fernet_v2()` 首次构造**（lru_cache 内），而非 Settings validator。
4. **非 prod 回退**：缺 key 时 fallback 到 `_fernet_v1()`，WARN 一次。

## 回滚注意

回滚到旧代码：enc2$ 行在旧 `decrypt_text` 下走 else 分支原样返回密文字符串。**回滚前必须确认无 enc2$ 存量或接受其不可读。**

## 测试结果

- `test_content_crypto.py`：16 passed
- 全量回归：1053 passed，0 failed
