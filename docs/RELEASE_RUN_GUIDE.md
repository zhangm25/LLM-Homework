# RoamMind 发布版运行说明

本文档面向“拿到发布包后如何安装和运行”的用户。发布包默认采用单端口部署：后端 FastAPI 同时提供 API 和前端页面，浏览器打开 `http://localhost:8000` 即可使用。

## 1. 发布包里有什么

典型发布包结构如下：

```text
RoamMind-0.1.0/
├─ backend/
│  ├─ app/                    # 后端运行代码
│  └─ requirements.txt         # Python 依赖
├─ frontend/
│  └─ dist/                    # 已构建好的前端静态文件
├─ config/
│  └─ roammind.env.example     # 外部配置模板，不包含真实 Key
├─ install_windows.ps1         # Windows 安装依赖
├─ start_roammind.ps1          # Windows 启动
├─ install_unix.sh             # macOS / Linux 安装依赖
├─ start_roammind.sh           # macOS / Linux 启动
└─ README_RUN.md               # 本说明的副本
```

发布包不会包含 `.env`、真实 API Key、`node_modules`、本地日志或 Git 历史。

## 2. 环境要求

- Python 3.11 或更高版本。
- Windows、macOS、Linux 均可运行。
- 发布包已经包含前端构建产物，普通用户不需要安装 Node.js。
- 如需真实大模型和地图能力，需要准备：
  - OpenAI-compatible LLM Key，例如 DeepSeek。
  - 高德 Web 服务 Key，用于后端 POI 搜索、路径规划、逆地理编码。
  - 可选：高德 Web 端 JS API Key 和安全密钥，用于浏览器里的真实交互地图。不填时会自动使用内置 SVG 地图兜底。

## 3. 配置文件

所有 Key 都放在程序包外部配置文件里。首次运行前，请复制模板：

### Windows PowerShell

```powershell
Copy-Item .\config\roammind.env.example .\config\roammind.env
notepad .\config\roammind.env
```

### macOS / Linux

```bash
cp config/roammind.env.example config/roammind.env
nano config/roammind.env
```

最小配置示例：

```env
LLM_PROVIDER=deepseek
LLM_API_KEY=你的_deepseek_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

AMAP_WEB_SERVICE_KEY=你的高德Web服务Key

VITE_AMAP_JS_KEY=你的高德Web端JSAPIKey
VITE_AMAP_JS_SECURITY_CODE=你的高德JS安全密钥

DEFAULT_CITY=北京
CORS_ORIGINS=*
```

说明：

- `LLM_API_KEY`、`AMAP_WEB_SERVICE_KEY` 只在后端读取，不会打进前端页面。
- `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_JS_SECURITY_CODE` 是浏览器侧公共配置，会通过 `/api/config` 下发给前端，用于加载高德 JS 地图。
- 不填写任何 Key 也可以启动，系统会进入降级/示例模式，但真实 POI、路线和 LLM 能力会受限。
- 如果你希望配置文件放在发布目录外，可以设置环境变量 `ROAMMIND_ENV_FILE` 指向它。

## 4. 安装依赖

### Windows PowerShell

在发布包根目录执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_windows.ps1
```

该脚本会创建 `.venv` 虚拟环境并安装后端依赖。

### macOS / Linux

```bash
chmod +x install_unix.sh start_roammind.sh
./install_unix.sh
```

## 5. 启动服务

### Windows PowerShell

```powershell
.\start_roammind.ps1
```

指定配置文件或端口：

```powershell
.\start_roammind.ps1 -ConfigPath "D:\configs\roammind.env" -Port 8000
```

### macOS / Linux

```bash
./start_roammind.sh
```

指定配置文件或端口：

```bash
ROAMMIND_ENV_FILE=/opt/roammind/roammind.env PORT=8000 ./start_roammind.sh
```

启动成功后打开：

```text
http://localhost:8000
```

局域网访问时，将 `localhost` 换成运行机器的 IP，并确保防火墙放行端口。

## 6. 常见问题

### 页面能打开，但显示示例模式

检查配置文件中是否填写了 `LLM_API_KEY` 和 `AMAP_WEB_SERVICE_KEY`，以及启动服务的终端是否显示正在使用正确的 `ROAMMIND_ENV_FILE`。

### 地图不是高德真实地图，而是 SVG 地图

检查 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_JS_SECURITY_CODE`。如果为空或高德控制台的域名白名单不允许当前访问地址，前端会回退到 SVG 地图。

### 浏览器定位不可用

多数浏览器只允许 HTTPS 或 localhost 使用定位。局域网 IP 访问时，浏览器可能拒绝定位权限。可以在对话框里直接说“从 XX 出发”，或在顶部搜索起点。

### API Key 是否会进入发布包

不会。打包脚本不会复制 `.env`，发布包只包含 `config/roammind.env.example` 模板。真实配置由运行者自己创建或通过 `ROAMMIND_ENV_FILE` 指定。

## 7. 从源码重新生成发布包

开发者可在源码仓库根目录执行：

```bash
python scripts/build_release.py
```

脚本会：

1. 用空的前端运行时变量构建 `frontend/dist`，避免把本机 Key 打进前端包。
2. 生成 `dist_release/RoamMind-0.1.0/`。
3. 生成 `dist_release/RoamMind-0.1.0.zip`。
