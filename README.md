# Warehouse Inventory Server

一个可压缩转发的 Python 仓库表单程序。首次运行会自动创建本地数据库、原始表单 CSV、库存明细 XLSX 和每周仓检报表目录。

## 运行

Windows:

```bat
run_windows.bat
```

或手动运行:

```bash
python app.py
```

默认访问地址:

- 本机: `http://127.0.0.1:8088`
- 局域网: 程序启动后会在控制台显示

默认账号:

- 账号: `admin`
- 密码: `admin123`

首次登录后建议在“用户”页面新建正式账号，并停止使用默认密码。

## 功能

- 登录 / 注册：注册会创建普通用户，管理员可在“用户”页面创建管理员或普通用户。
- 入库表单：记录商品类别、名称、数量、位置、备注和时间。
- 仓库检索：按名称、类别、位置、日期检索，并自动记录搜索频率。
- BOM 对照：拖拽上传 `.csv`、`.xlsx`、`.txt`，自动读取器件名称、类别和数量，与库存对照后生成采购表单。
  - 已适配嘉立创 BOM 常见导出格式：会跳过标题说明行，自动识别 `Quantity`、`Comment`、`Designator`、`Footprint`、`Manufacturer Part`、`Supplier Part` 等列。
  - 采购明细会保留规格、封装、LCSC 编号和位号，便于直接核对和下单。
  - 电阻/电容会做规格归一化：例如 `0603WAF1003T5E` 可转译为 `100kΩ`，再与库存里手写的 `100kΩ/欧姆` 对照。
  - 如果 BOM 中有 `C25803` 这类 LCSC 编号，系统会从立创商城公开搜索页补全型号、名称、品牌、封装、库存、价格和商品链接，并缓存到 `data/lcsc/lcsc_products.json` 与 `data/lcsc/lcsc_products.csv`。
- LCSC 缓存：管理员可以在“仓检报表”页面手动刷新缓存，并下载 `LCSC 商品缓存` CSV。
- 统计清理：系统会定期清理 BOM 标题行、纯封装名、空分类等异常统计，避免高需求页面被脏数据污染。
- 统计排行：展示高频搜索器件、高需求采购器件，以及用户 BOM 器件总用量排行。
- 管理员后台：配置端口、数据目录、固定公网地址、内网穿透开关、注册开关、统计阈值，并查看访问日志。
- 周检报表：每周日自动生成近 7 天入库汇总和全量库存对照。

## 数据位置

默认数据目录:

```text
data/
```

主要文件:

- `data/inventory.db.enc`: 启用加密后的用户账号和仓库记录数据库
- `data/inventory.db`: 旧版或关闭加密时的数据库
- `data/forms/warehouse_records.csv`: 原始入库表单
- `data/reports/all_inventory_detail.xlsx`: 全量库存明细
- `data/reports/weekly_summary_YYYY-MM-DD.xlsx`: 每周日自动生成的近 7 天汇总
- `data/reports/all_inventory_compare_YYYY-MM-DD.xlsx`: 全量库存对照表
- `data/uploads/`: 上传的 BOM 原始文件
- `data/purchase_orders/`: 自动生成的采购表单
- `data/lcsc/`: LCSC 商品编号补全缓存
- `server_config.json`: 管理员后台保存的可重启生效配置

生产环境默认启用 `/data` 静态加密，需要设置并长期保存 `WAREHOUSE_DATA_KEY`。详见 `DATA_SECURITY.md`。

如需指定服务器内部文档目录:

```powershell
$env:WAREHOUSE_DATA_DIR="D:\warehouse-data"
python app.py
```

## 内网穿透

程序会自动尝试以下方式:

1. 如果设置了 `WAREHOUSE_PUBLIC_URL`，直接在页面显示这个固定地址。
2. 如果本机安装了 `cloudflared`，会尝试启动临时 Cloudflare Tunnel。
3. 如果本机安装了 `ngrok` 且设置了 `NGROK_AUTHTOKEN`，会尝试启动 ngrok。

关闭自动穿透:

```powershell
$env:WAREHOUSE_TUNNEL="0"
python app.py
```

修改端口:

```powershell
$env:WAREHOUSE_PORT="8090"
python app.py
```

也可以在网站内用管理员账号进入“后台配置”保存端口。端口、数据目录和穿透方式保存后需要重启程序生效。

## 打包转发

运行:

```bash
python build_zip.py
```

生成的 `warehouse_inventory_app.zip` 可直接发送给其他电脑。解压后运行 `run_windows.bat`。

## Docker 部署

Docker 部署时，程序进入镜像，真实业务数据通过 `./data:/app/data` 挂载到容器。

迁移已有服务器数据时，先备份旧服务器的 `data/` 目录，再恢复到新服务器项目目录的 `./data`。

服务器从 GitHub 更新程序：

```bash
sh ./update_server.sh
```

Windows Server：

```powershell
powershell -ExecutionPolicy Bypass -File .\update_server.ps1
```

详细步骤见：

```text
DOCKER_DEPLOY.md
```
