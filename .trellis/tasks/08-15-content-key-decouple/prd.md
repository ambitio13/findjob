# 内容加密密钥与认证签名密钥解耦

## 依赖声明

- **Depends on**: 无（Phase 1）。
- **Blocks**: `08-11-showcase-demo-deployment` R3 的 `.env.prod.example` 定稿
  （该文件需包含本任务新增的 `CONTENT_ENCRYPTION_KEY`）。展示部署演练
  （Phase G）应在本任务合入后进行，否则线上加密数据将绑定在
  `AUTH_SECRET_KEY` 派生密钥上，之后再解耦要迁数据。

## Background（漏洞 C1）

`app/core/content_crypto.py` 的 Fernet 密钥**从 `AUTH_SECRET_KEY` 派生**，一个
secret 双用途：

- 泄露 `AUTH_SECRET_KEY` = 既可伪造任意用户 token，**又可解密全部加密的
  `jd_raw` 等内容**（两个风险域一把钥匙）；
- 怀疑 token 密钥泄露而轮换 `AUTH_SECRET_KEY` = 所有已加密内容不可解密，
  数据全毁（想轮换都不敢）。

密钥轮换本身在 08-11 中是 out of scope（可接受），但两个风险域的耦合是规划外
的设计缺陷。

## Goal

内容加密使用独立的 `CONTENT_ENCRYPTION_KEY`；`AUTH_SECRET_KEY` 只用于签名。
存量密文无缝兼容，不丢数据。

## Requirements

- R1 新增配置 `CONTENT_ENCRYPTION_KEY`（Fernet 兼容的 32 字节 urlsafe base64）。
- R2 密文版本化：新写入用 `enc2$<token>` 前缀；`enc1$`（现前缀）解析时按旧
  逻辑从 `AUTH_SECRET_KEY` 派生密钥解密，实现只读兼容。
- R3 读取路径双密钥兼容：`decrypt_text` 先按前缀选密钥；无前缀的 legacy 明文
  行为不变。
- R4 生成工具：提供一条命令（如 `python -c ...` 或管理脚本）生成
  `CONTENT_ENCRYPTION_KEY`，写进 `.env.prod.example` 注释与 runbook。
- R5 配置缺失行为：`APP_ENV=prod` 下 `CONTENT_ENCRYPTION_KEY` 未设置时启动
  即报错（fail-fast，防止回退到派生密钥继续写入 enc1$）；local/test 未设置时
  允许沿用旧派生路径并在日志 WARN 一次。
- R6 测试：enc1$ 存量可解、新写入为 enc2$、双 key 共存环境往返正确、prod 缺
  key 启动失败。

## Acceptance Criteria

- [ ] AC1：构造 `enc1$` 存量密文（用旧派生密钥），在新代码下可正确解密。
- [ ] AC2：配置 `CONTENT_ENCRYPTION_KEY` 后新加密输出为 `enc2$` 前缀，且能解密。
- [ ] AC3：`APP_ENV=prod` 且未配置该 key 时应用启动失败，错误信息指明变量名。
- [ ] AC4：`.env.prod.example` / `.env.example` 与 runbook 含生成与配置说明。
- [ ] AC5：现有 content_crypto 相关测试全绿，无行为回归。

## Constraints

- 不做密钥轮换体系（out of scope 不变）；只做密钥域解耦 + 版本前缀为将来轮换
  留口。
- 迁移脚本（存量 enc1$ 批量重加密为 enc2$）为可选加分项，不做强制。
