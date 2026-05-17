# 仓库管理系统运行说明

## 1. 项目位置

当前项目根目录：

```text
C:\Users\ROG\OneDrive\Documents\New project
```

主要文件：

```text
app.py                         主程序
run_windows.bat                Windows 一键启动脚本
build_zip.py                   生成转发压缩包
warehouse_inventory_app.zip    可转发压缩包
WAREHOUSE_USER_MANUAL.md       仓管系统详细使用说明
static/                        前端样式和图片资源
data/                          数据库、报表、BOM、采购单数据
```

完整业务操作流程请优先阅读：

```text
WAREHOUSE_USER_MANUAL.md
```

## 2. 运行前准备

电脑需要安装 Python 3.10 或以上版本。

检查 Python 是否可用：

```powershell
python --version
```

如果提示找不到 Python，请先安装 Python，并勾选：

```text
Add Python to PATH
```

## 3. 一键运行

双击项目根目录里的：

```text
run_windows.bat
```

启动成功后，浏览器访问：

```text
http://127.0.0.1:8088
```

默认管理员账号：

```text
账号：admin
密码：admin123
```

首次正式使用建议登录后到“用户”页面创建新管理员账号，并停止使用默认密码。

## 4. 手动运行

也可以打开 PowerShell，进入项目目录：

```powershell
cd "C:\Users\ROG\OneDrive\Documents\New project"
python app.py
```

看到类似下面内容就说明启动成功：

```text
Warehouse Inventory Server
Local:   http://127.0.0.1:8088
LAN:     http://你的局域网IP:8088
Data:    C:\Users\ROG\OneDrive\Documents\New project\data
```

## 5. 局域网访问

同一个 Wi-Fi 或局域网内的其他设备，可以访问启动日志里的 LAN 地址，例如：

```text
http://192.168.1.20:8088
```

如果无法访问，检查 Windows 防火墙是否拦截了 Python。

## 6. 后台配置

管理员登录后进入：

```text
后台配置
```

可以配置：

- 网站端口
- 监听主机
- 数据目录
- 固定公网地址
- 内网穿透开关
- 是否允许用户注册
- 高频搜索阈值
- 高需求采购阈值
- 每周仓检日
- 访问日志查看

注意：端口、数据目录、穿透方式保存后，需要重启程序才会生效。

Windows 服务器环境下，优先双击 `run_windows.bat`。它会默认监听 `0.0.0.0` 并把穿透模式切到自动；如果机器安装了 `cloudflared` 或 `ngrok`，会尝试生成外部访问地址。若都没有安装，仍可直接用服务器局域网 IP 访问。

首次启动时如果没有设置管理员密码，`run_windows.bat` 会提示输入 `admin` 账号的新密码。启动后控制台里的 `Bind: 0.0.0.0:8088` 表示服务对局域网开放；`Local: http://127.0.0.1:8088` 只是服务器本机浏览器使用的地址，不代表只能本机访问。

## 7. BOM 对照使用

进入：

```text
BOM对照
```

上传或拖拽文件：

```text
.csv
.xlsx
.txt
```

系统会自动读取 BOM 中的：

- 商品/器件名称
- 数量
- 规格
- 封装
- LCSC 编号
- 位号

然后和仓库库存对照，自动生成采购清单。

如果 BOM 中存在 `C25803` 这类 LCSC 编号，系统会自动访问立创商城公开搜索页补全商品信息，并缓存到独立文件：

```text
data/lcsc/lcsc_products.json
data/lcsc/lcsc_products.csv
```

缓存字段包括：

- LCSC 编号
- 商品型号
- 商品名称
- 商品分类
- 品牌
- 封装
- 库存
- 最低价格
- 商品链接
- 更新时间

报表页面里也可以下载 `LCSC 商品缓存`。

管理员还可以在“仓检报表”页面点击：

```text
刷新 LCSC 缓存
```

系统会从已经上传过的 BOM 和采购单中重新提取 C 编号，并补全还没有缓存的商品信息。

已适配嘉立创 BOM 常见导出格式，例如：

```text
Quantity
Comment
Designator
Footprint
Manufacturer Part
Supplier Part
```

电阻型号也会做转译匹配，例如：

```text
0603WAF1003T5E -> 100kΩ
0603WAF4702T5E -> 47kΩ
FRC0603F8871TS -> 8.87kΩ
1206W4F1004T5E -> 1MΩ
```

所以库存里手写 `100kΩ/欧姆`，也可以和 BOM 型号进行对照。

## 8. 数据保存位置

默认数据目录：

```text
data/
```

重要数据：

```text
data/inventory.db.enc             加密后的数据库
data/inventory.db                 旧版或关闭加密时的数据库
data/forms/warehouse_records.csv  原始入库记录
data/reports/                     周检报表和全量库存表
data/uploads/                     上传的 BOM 原文件
data/purchase_orders/             自动生成的采购清单
```

生产环境默认启用数据加密，启动前需要设置 `WAREHOUSE_DATA_KEY`。密钥必须单独备份，换服务器时沿用同一个密钥。

## 9. 停止服务

如果是用 PowerShell 运行的，按：

```text
Ctrl + C
```

如果是双击 `run_windows.bat` 启动的，关闭对应的黑色命令行窗口即可。

## 10. 修改端口

临时修改端口运行：

```powershell
$env:WAREHOUSE_PORT="8090"
python app.py
```

然后访问：

```text
http://127.0.0.1:8090
```

也可以在管理员后台配置端口，保存后重启程序。

## 11. 指定数据目录

如果想把数据库和报表保存到别的位置：

```powershell
$env:WAREHOUSE_DATA_DIR="D:\warehouse-data"
python app.py
```

## 12. 重新打包转发

运行：

```powershell
python build_zip.py
```

会生成：

```text
warehouse_inventory_app.zip
```

把这个压缩包发给其他电脑，对方解压后双击：

```text
run_windows.bat
```

即可运行。

## 13. 常见问题

### 端口被占用

如果 `8088` 被占用，换一个端口：

```powershell
$env:WAREHOUSE_PORT="8090"
python app.py
```

### 页面打不开

先确认程序窗口没有关闭，然后访问：

```text
http://127.0.0.1:8088
```

### 局域网设备打不开

检查：

- 两台设备是否在同一个网络
- Windows 防火墙是否允许 Python 通信
- 是否访问了启动日志里的 LAN 地址

### 忘记管理员密码

目前没有做网页重置密码功能。可以删除测试环境里的：

```text
data/inventory.db
```

然后重新运行程序，系统会重新创建默认管理员账号。

注意：删除数据库会清空用户和库存数据，正式环境不要直接删除。

## 13. 本次新增：立创分类与自动补全

进入“填写表单”后，现在可以使用两种方式入库：

```text
方式一：选择一级分类，系统会自动生成二级分类选项。
方式二：只填写 LCSC 编号，例如 C25803，再点击“获取 LCSC 信息”。
```

联网补全会自动填写：

```text
商品名称、商品类别、规格/阻容值、封装、额定值/功率、品牌、LCSC 商品链接
```

立创商城分类缓存位置：

```text
data/lcsc/lcsc_categories.json
```

立创商品缓存位置：

```text
data/lcsc/lcsc_products.json
data/lcsc/lcsc_products.csv
```

“仓库检索”页面新增了“同类器件子层级”，会把类似 `100nF 35V / 100nF 50V`、同阻值不同封装、同型号不同位置的库存聚合展示，方便看总量和变体。

“统计排行”页面新增 3D 库存类型分析，会从内部接口读取库存分类、采购缺口和低库存信息：

```text
http://127.0.0.1:8088/api/inventory_insights
```

“智能助手”页面提供文字版知识网络，点击任意节点即可固定查看详情：

```text
http://127.0.0.1:8088/assistant
http://127.0.0.1:8088/api/knowledge_graph
```

管理员可以在“后台配置”里填写库存智能助手 API：

```text
API 端口 / 地址：例如 http://127.0.0.1:8000/v1/chat/completions
模型名：按你的 API 服务填写
API Key：可留空，也可填写 Bearer Key
系统调教提示词：用于限制助手只回答库存、BOM、器件、数据手册研读等问题
```

配置会保存到：

```text
data/assistant_config.json
```

启用数据加密后，该配置文件会以密文落盘，页面仍可正常编辑和测试 API。

当你在填写表单里修改 LCSC 编号后，再点击“获取 LCSC 信息”，页面会重新刷新分类、名称、规格、封装、额定值、品牌和 LCSC 链接，不再保留上一次错误编号的预写入内容。
