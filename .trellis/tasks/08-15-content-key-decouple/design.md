# Design：内容加密密钥与认证签名密钥解耦

## 0. 边界

- **改动面**：`app/core/content_crypto.py`（双版本前缀 + 双密钥）、
  `app/core/config.py`（新字段 + prod 校验）、`.env.example` /
  `.env.prod.example`、runbook。
- **不改动**：`security.py`（token 签名路径）、任何调用
  `encrypt_text/decrypt_text` 的业务代码（接口签名不变）、DB schema。

## 1. 现状（已核实）

```python
ENCRYPTED_PREFIX = "enc1$"
_fernet(): key = urlsafe_b64encode(sha256(AUTH_SECRET_KEY 或 dev fallback))
encrypt_text: 非空且无前缀 → "enc1$" + token
decrypt_text: 无前缀 → 原样返回（legacy 明文）；
              解密失败 → 记日志、返回 ""（密文永不出现在调用方）
```

## 2. 目标形态

```
enc1$<token>  ← 旧：sha256(AUTH_SECRET_KEY) 派生（只读兼容）
enc2$<token>  ← 新：CONTENT_ENCRYPTION_KEY 直接作 Fernet key
<无前缀>      ← legacy 明文，原样返回（不变）
```

## 3. 核心决策

### 3.1 双前缀 + 按前缀选密钥（读兼容），不做一次性迁移

- 存量 `enc1$` 行**原样保留**，读取时用旧派生密钥解；新写入一律 `enc2$`。
- 否决"启动时全表重加密迁移"：需要遍历所有加密列（jd_raw 等）、锁表
  风险、失败回滚复杂；展示环境数据量小但没必要引入迁移风险。PRD 已把
  批量重加密列为可选加分项。
- 否决"同前缀换密钥"：`enc1$` 密文在新密钥下解密必然 InvalidToken →
  现有"失败返回空串"行为会静默吞数据，属隐蔽数据丢失，必须用前缀区分。

### 3.2 新密钥直接用作 Fernet key（不再哈希派生）

`CONTENT_ENCRYPTION_KEY` 必须是 Fernet 规范密钥（32 字节 urlsafe
base64，由 `Fernet.generate_key()` 生成）。旧路径 sha256 派生是因为
`AUTH_SECRET_KEY` 是任意字符串；新变量从生成之日起就按 Fernet 规范，
无需派生、无损熵。配置校验在加载时 `Fernet(key)` 试构造，格式错即启动
失败并指明变量名。

### 3.3 prod 缺 key 启动失败（fail-fast），local/test 回退旧路径

- `app_env == "prod"` 且 `CONTENT_ENCRYPTION_KEY` 为空：启动即
  `RuntimeError`。必须 fail-fast：否则静默回退派生密钥继续写 `enc1$`，
  数据重新绑回 `AUTH_SECRET_KEY`，解耦失败且无人知晓。
- 非 prod 且为空：沿用现有派生路径（含现有 `_DEV_FALLBACK_SECRET`
  行为），首次使用时 WARN 一次提示配置新 key——本地体验零变化。

### 3.4 校验位置：`content_crypto` 模块级检查，而非 Settings validator

失败点放在首次 `_fernet_v2()` 构造（`lru_cache` 内），与现有 `_fernet()`
的错误风格一致（同文件 `RuntimeError` 先例）。否决 Settings
`model_validator`：config 是通用层，不应感知内容加密的领域规则；且
worker/api 两个入口都会触达加密调用，延迟失败点一致。

## 4. 实现草图

```python
_PREFIX_V1, _PREFIX_V2 = "enc1$", "enc2$"

@lru_cache(maxsize=1)
def _fernet_v1() -> Fernet: ...   # 现有 _fernet 原样搬入

@lru_cache(maxsize=1)
def _fernet_v2() -> Fernet:
    settings = get_settings()
    key = settings.content_encryption_key
    if not key:
        if settings.app_env == "prod":
            raise RuntimeError("CONTENT_ENCRYPTION_KEY must be configured ...")
        _log.warning("content_crypto.legacy_key_in_use")   # 一次性
        return _fernet_v1()
    return Fernet(key.encode("ascii"))                      # 格式错自然抛

def encrypt_text(plain):
    ...  # 判重前缀扩展为 enc1$/enc2$ 均跳过；输出 enc2$ + v2 加密

def decrypt_text(value):
    if value.startswith(_PREFIX_V1): 用 v1 解
    elif value.startswith(_PREFIX_V2): 用 v2 解
    else: return value            # legacy 明文
    # InvalidToken 行为不变：log error + 返回 ""
```

## 5. 生成与配置

- 生成命令（写进 `.env.example` 注释与 runbook）：

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `.env.prod.example`：`CONTENT_ENCRYPTION_KEY=<必填>` + 注释
  （与 `AUTH_SECRET_KEY` 分开保存均可轮换；丢失 = 加密内容不可恢复，
  需与 `.env` 一起备份——呼应 08-11 out-of-scope 的备份取舍，注释明示）。
- `__all__` 导出 `ENCRYPTED_PREFIX_V2` 备用（如 #5 之外的运维脚本）。

## 6. 测试设计

- `enc1$` 用旧派生密钥构造 → 新代码可解（AC1）；
- 配置新 key 后 `encrypt_text` 输出 `enc2$` 且往返正确；已有 `enc1$`
  密文混存场景各自正确解密（AC2）；
- prod 缺 key：调用 `encrypt_text` 抛 `RuntimeError` 且信息含变量名（AC3，
  注意 monkeypatch settings 后清 `lru_cache`）；
- legacy 明文 / 空串 / 解密失败（错误 key 的 enc2$）行为与现状一致；
- 既有 `test_content_crypto*`（如存在）全绿，无前缀断言扩散。

## 7. 兼容性与回滚

- 读路径向后兼容三代数据（明文/enc1$/enc2$）；写路径只产 enc2$。
- 回滚到旧代码：enc2$ 行在旧代码下按"无 enc1$ 前缀"判定为明文原样返回
  → 密文泄露给调用方？**否**——旧 `decrypt_text` 只认 `enc1$` 前缀，
  `enc2$` 走 else 分支原样返回密文字符串。这是**已知回滚风险**，回滚前
  必须确认无 enc2$ 存量或接受其不可读。写进 implement.md 回滚注意项。
