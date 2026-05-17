# 服务器一键部署说明

## 1. 解压

将 `warehouse_inventory_app.zip` 上传到服务器后解压到目标目录，例如：

```powershell
Expand-Archive .\warehouse_inventory_app.zip -DestinationPath C:\warehouse_inventory -Force
cd C:\warehouse_inventory
```

## 2. 启动

必须设置正式管理员密码。

PowerShell 推荐方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_server.ps1 -AdminPassword "换成强密码"
```

指定监听地址、端口和数据目录：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_server.ps1 -HostName "127.0.0.1" -Port 8088 -DataDir "D:\warehouse-data" -AdminPassword "换成强密码"
```

也可以使用批处理：

```bat
set WAREHOUSE_ADMIN_PASSWORD=换成强密码
set WAREHOUSE_DATA_KEY=换成数据加密密钥
run_server.bat
```

Linux/macOS 服务器：

```bash
export WAREHOUSE_ADMIN_PASSWORD='换成强密码'
export WAREHOUSE_DATA_KEY='换成数据加密密钥'
sh ./start_server.sh
```

数据加密密钥生成示例：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`WAREHOUSE_DATA_KEY` 必须长期保存。系统会把 `/data` 下的数据库、配置、上传文件和报表加密落盘，换服务器时要使用同一个密钥。

## 3. 访问

默认地址：

```text
http://127.0.0.1:8088
```

默认管理员账号：

```text
admin
```

密码是启动时设置的 `WAREHOUSE_ADMIN_PASSWORD`。

## 4. 公网部署建议

生产环境建议保持应用只监听本机：

```text
WAREHOUSE_HOST=127.0.0.1
```

然后用 Nginx、Caddy 或 IIS 做 HTTPS 反向代理。不要直接把 Python 服务裸露到公网。

## 5. 数据目录

默认数据目录是解压目录下的 `data/`。正式使用建议指定独立目录，例如：

```text
D:\warehouse-data
```

需要定期备份这个目录，里面包含：

- `inventory.db.enc`（启用加密后）
- `inventory.db`（旧版或关闭加密时）
- BOM 上传文件
- PCB/采购/报表/用户知识库等运行数据

## 6. 更新部署

更新程序时，只替换解压目录中的程序文件，不要覆盖正式 `data/` 目录。若使用独立 `WAREHOUSE_DATA_DIR`，更新更安全。

## 7. 热补丁维护

在开发电脑生成补丁包：

```powershell
python make_patch.py --all --name notice-ui
```

如果只想指定文件：

```powershell
python make_patch.py --files app.py static/app.css static/inventory.js CHANGELOG.md --name notice-ui
```

把 `patches/warehouse_patch_*.zip` 上传到服务器应用目录，然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\apply_patch.ps1 -PatchZip .\warehouse_patch_YYYYMMDD_HHMMSS_notice-ui.zip -AdminPassword "当前管理员强密码"
```

如果正式数据目录是独立目录：

```powershell
powershell -ExecutionPolicy Bypass -File .\apply_patch.ps1 -PatchZip .\warehouse_patch_YYYYMMDD_HHMMSS_notice-ui.zip -DataDir "D:\warehouse-data" -AdminPassword "当前管理员强密码"
```

补丁脚本会自动：

- 停止当前端口上的旧 `app.py`
- 备份即将覆盖的程序文件到 `_patch_backups/`
- 覆盖补丁文件
- 执行 `python -m py_compile app.py`
- 重启服务并检查 `/login`
- 如果失败，自动恢复备份并重新启动旧版本

补丁包会拒绝覆盖 `data/`、`tmp/`、`logs/`、`_patch_backups/` 等运行数据目录。
