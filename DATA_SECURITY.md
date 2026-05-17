# 数据加密与密钥管理

本系统默认启用 `/data` 静态加密保护，核心数据使用 AES-256-GCM 加密。服务运行时仍可正常检索库存、BOM 和比赛备件；磁盘上的数据文件保持密文。

## 必须保存的密钥

生产环境必须设置：

```text
WAREHOUSE_DATA_ENCRYPTION=1
WAREHOUSE_DATA_KEY=你的数据加密密钥
```

生成密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Windows PowerShell：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`WAREHOUSE_DATA_KEY` 必须单独备份，不能只放在 `data/` 目录里。密钥丢失后，`inventory.db.enc` 和其他已加密数据无法恢复。

## 首次升级迁移

旧版本如果已经有明文 `data/inventory.db` 和上传文件，新版本首次启动时会自动迁移：

```text
data/inventory.db      -> data/inventory.db.enc
data/*.json/csv/xlsx   -> 加密后的同名文件
data/uploads/*         -> 加密后的同名文件
```

迁移完成后，业务功能仍通过系统页面访问，不要用文本编辑器或 SQLite 工具直接打开 `data/` 里的密文文件。

## Docker 迁移要求

迁移到新服务器时需要同时迁移：

```text
data/
.env 中的 WAREHOUSE_DATA_KEY
```

如果备份包里存在：

```text
data/inventory.db.enc
```

新服务器必须使用旧服务器同一个 `WAREHOUSE_DATA_KEY`。换了密钥会导致数据库无法解密。

## 密码说明

用户登录密码不会被可逆加密保存，而是保存为不可逆哈希。即使管理员也不能从数据库还原用户密码，只能重置密码。这是密码存储的正确方式。

当前密码哈希使用 PBKDF2-SHA256，600000 次迭代。旧账号如果仍是较低迭代次数，会在用户下次登录成功后自动升级。
