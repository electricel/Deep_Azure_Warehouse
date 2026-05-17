# Docker 部署与真实数据迁移说明

本文档说明如何把已经上线并录入真实数据的仓管系统迁移到新服务器，并用 Docker 一键部署。

核心原则：

```text
程序打进 Docker 镜像。
真实业务数据不要打进镜像。
真实数据通过服务器目录 ./data 挂载到容器 /app/data。
```

这样做的好处：

- 后续升级镜像不会覆盖数据库。
- 数据目录可以单独备份。
- 迁移服务器时只需要复制 `data/`。
- 容器删除后数据仍保留在宿主机。

## 1. 旧服务器备份真实数据

推荐方式是使用完整迁移备份脚本。它会把程序文件和真实 `data/` 目录一起打包，并用 SQLite 在线备份 API 复制正在运行中的 `inventory.db`，比直接复制数据库文件更稳。

在旧服务器应用目录执行：

```powershell
python backup_for_docker.py
```

Windows 服务器也可以直接双击或执行：

```bat
backup_for_docker.bat
```

如果正式数据目录不是应用目录下的 `data/`，请指定：

```powershell
python backup_for_docker.py --data-dir "D:\warehouse-data"
```

生成的完整迁移包在：

```text
backups/warehouse_docker_full_backup_YYYYMMDD_HHMMSS.zip
```

这个 zip 已经包含：

```text
app/                  程序和 Docker 部署文件
app/data/             真实业务数据
BACKUP_MANIFEST.json  备份来源信息
RESTORE_DOCKER.md     快速恢复说明
```

把这个完整迁移包上传到新服务器，解压后进入 `app/`，配置 `.env`，再执行 `docker compose up -d --build` 即可。

说明：`inventory.db` 会通过 SQLite 在线备份生成一致快照；上传文件、报表、BOM 原文件等普通文件会按备份时刻复制。为了避免有人正在上传大文件时复制到半成品，建议在低峰期执行，或临时通知用户暂停上传。

### 1.1 手动备份方式

旧服务器上先停止正在运行的仓管系统，避免数据库复制时仍在写入。

Windows 服务器：

```bat
stop.bat
```

或直接关闭运行 `app.py` 的命令行窗口。

然后备份旧服务器的正式数据目录。如果你之前没有单独指定数据目录，通常是应用目录下：

```text
data/
```

如果之前用过独立目录，例如：

```text
D:\warehouse-data
```

就备份这个目录。

Windows PowerShell 示例：

```powershell
Compress-Archive -Path .\data\* -DestinationPath .\warehouse-data-backup.zip -Force
```

独立数据目录示例：

```powershell
Compress-Archive -Path D:\warehouse-data\* -DestinationPath D:\warehouse-data-backup.zip -Force
```

备份包内应至少包含：

```text
inventory.db
forms/
reports/
uploads/
purchase_orders/
lcsc/
competition_materials/
user_knowledge/
```

## 2. 新服务器准备 Docker

新服务器需要安装：

- Docker Engine
- Docker Compose 插件

Linux 检查：

```bash
docker --version
docker compose version
```

Windows Server 检查：

```powershell
docker --version
docker compose version
```

## 3. 上传程序文件

把项目程序上传到新服务器。至少需要这些文件和目录：

```text
app.py
Dockerfile
docker-compose.yml
.dockerignore
.env.example
requirements.txt
docker_run.sh
docker_run.ps1
static/
public/
CHANGELOG.md
README.md
HOW_TO_RUN.md
SERVER_DEPLOY.md
WAREHOUSE_USER_MANUAL.md
sample_bom.csv
```

不要把开发机上的 `data/`、`tmp/`、`.git/`、`node_modules/` 上传为程序镜像内容。

## 4. 恢复真实数据到新服务器

在新服务器项目目录下创建：

```text
data/
```

然后把旧服务器备份的数据解压进去。

Linux 示例：

```bash
mkdir -p data
unzip warehouse-data-backup.zip -d data
```

Windows PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force -Path .\data
Expand-Archive .\warehouse-data-backup.zip -DestinationPath .\data -Force
```

恢复完成后，新服务器项目目录结构应类似：

```text
docker-compose.yml
Dockerfile
app.py
static/
public/
data/
  inventory.db
  uploads/
  reports/
  lcsc/
  competition_materials/
```

## 5. 配置管理员密码

复制环境变量示例文件：

Linux：

```bash
cp .env.example .env
nano .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
notepad .env
```

把 `.env` 里的密码改成强密码：

```text
WAREHOUSE_PORT=8088
WAREHOUSE_ADMIN_USER=admin
WAREHOUSE_ADMIN_PASSWORD=换成强密码
WAREHOUSE_DATA_ENCRYPTION=1
WAREHOUSE_DATA_KEY=换成数据加密密钥
```

不要保留默认占位值 `change-this-strong-password`。一键脚本检测到默认占位值时会拒绝启动。

注意：容器启动时如果提供了 `WAREHOUSE_ADMIN_PASSWORD`，系统会把已有 `admin` 账号密码同步更新为这个密码。迁移后忘记旧密码也可以用这里的新密码登录。

`WAREHOUSE_DATA_KEY` 用于解密 `data/inventory.db.enc` 和其他 `/data` 加密文件，必须单独备份。生成示例：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 6. 一键启动

Linux：

```bash
sh ./docker_run.sh
```

或手动执行：

```bash
docker compose up -d --build
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\docker_run.ps1
```

或手动执行：

```powershell
docker compose up -d --build
```

## 7. 访问系统

浏览器访问：

```text
http://新服务器IP:8088
```

如果 `.env` 中改了端口，例如：

```text
WAREHOUSE_PORT=8090
```

则访问：

```text
http://新服务器IP:8090
```

## 8. 常用 Docker 命令

查看容器状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f warehouse
```

重启服务：

```bash
docker compose restart warehouse
```

停止服务：

```bash
docker compose down
```

重新构建并启动：

```bash
docker compose up -d --build
```

进入容器：

```bash
docker compose exec warehouse sh
```

## 8.1 从 GitHub 一键更新

服务器目录如果是通过 `git clone` 拉取的，可以直接执行：

```bash
sh ./update_server.sh
```

脚本会自动备份当前 `data/` 和 `.env` 到 `backups/`，然后执行 `git pull --ff-only`、重新构建 Docker 容器，并输出容器状态和最近日志。

Windows Server：

```powershell
powershell -ExecutionPolicy Bypass -File .\update_server.ps1
```

注意：`.env` 和 `data/` 只留在服务器本地，不要上传到 GitHub。

## 9. Docker 数据备份

Docker 部署后，真实数据仍在宿主机：

```text
./data
```

备份这个目录即可。

Linux：

```bash
tar -czf warehouse-data-$(date +%Y%m%d-%H%M%S).tar.gz data
```

Windows PowerShell：

```powershell
Compress-Archive -Path .\data\* -DestinationPath .\warehouse-data-backup.zip -Force
```

## 10. Docker 升级流程

升级程序时：

1. 备份 `data/`。
2. 替换程序文件。
3. 保留 `.env`。
4. 保留 `data/`。
5. 执行：

```bash
docker compose up -d --build
```

不要删除 `data/`，不要把空数据目录覆盖到服务器真实数据目录。

## 11. 防火墙与端口

如果浏览器打不开：

1. 确认容器运行：

```bash
docker compose ps
```

2. 确认端口映射：

```text
0.0.0.0:8088->8088/tcp
```

3. 检查服务器防火墙是否放行 8088。

4. 云服务器还需要检查安全组是否放行 8088。

生产公网环境建议使用 Nginx、Caddy 或宝塔反向代理 HTTPS，不建议直接裸露 Python 服务到公网。

## 12. 最小迁移检查清单

迁移前：

- 旧服务已停止。
- 旧 `data/` 已完整备份。
- 备份包里有 `inventory.db` 或 `inventory.db.enc`。
- 如果是 `inventory.db.enc`，已经记录旧服务器的 `WAREHOUSE_DATA_KEY`。

新服务器：

- Docker 已安装。
- 程序文件已上传。
- 旧数据已恢复到 `./data`。
- `.env` 已设置强密码和 `WAREHOUSE_DATA_KEY`。
- `docker compose up -d --build` 已执行。

启动后：

- 浏览器能打开 `/login`。
- `admin` 能用 `.env` 中的新密码登录。
- 仓库检索能查到旧数据。
- BOM 上传历史和报表目录仍存在。
- 比赛备件仓库数据仍存在。
