# 智能路线规划助手 Demo

这是一个面向自然语言出行需求的任务导向路线规划 Demo。用户可以输入类似：

- `到北京南站，顺路吃饭`
- `去五棵松看演唱会，顺便吃饭`
- `去奥森公园跑步，顺便吃饭`

系统会完成意图解析、任务拆解、候选地点搜索、路线规划、方案排序和推荐理由展示。

## 项目功能

- 支持中文自然语言输入。
- 支持浏览器定位作为路线起点。
- 支持识别明确目的地，例如北京南站、华熙LIVE·五棵松、奥林匹克森林公园。
- 支持“顺路吃饭”等中途任务。
- 后端接入高德 Web 服务：
  - 地理编码
  - 周边 POI 搜索
  - 驾车路线规划
  - 公交/地铁换乘路线规划
- 前端接入高德 JS 地图，用于展示路线和地点。
- 如果高德 API 不可用，会自动使用 mock 数据兜底，保证 Demo 可以运行。

## 技术栈

- 前端：React + Vite
- 后端：FastAPI
- 地图服务：高德 Web 服务 API + 高德 JS API
- LLM：兼容 OpenAI Chat Completions 格式的模型接口，可选
- 配置管理：`.env`

## 目录结构

```text
LLM-Homework/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                  # FastAPI 入口
│  │  ├─ schemas.py               # 请求/响应数据结构
│  │  └─ services/
│  │     ├─ intent_parser.py       # 意图解析，LLM 不可用时使用规则兜底
│  │     ├─ task_planner.py        # 任务拆解到 POI 类型
│  │     ├─ amap_client.py         # 高德 Web 服务调用封装
│  │     ├─ amap_planner.py        # 高德 POI + 路线规划主逻辑
│  │     ├─ mock_planner.py        # mock 兜底路线
│  │     └─ plan_ranker.py         # 候选方案排序
│  ├─ .env                         # 本地真实后端配置，不提交 Git
│  ├─ .env.example                 # 后端配置模板
│  └─ requirements.txt
├─ frontend/
│  ├─ src/
│  │  ├─ App.jsx                   # 前端主界面
│  │  ├─ main.jsx
│  │  └─ styles.css
│  ├─ .env                         # 本地真实前端配置，不提交 Git
│  ├─ .env.example                 # 前端配置模板
│  └─ package.json
└─ README.md
```

## 环境变量说明

`.env.example` 是模板文件，用来说明需要哪些配置。

`.env` 是本地真实配置文件，程序运行时读取它。真实 API Key 应该写在 `.env` 中，不要提交到 Git。

### 后端配置

文件位置：

```text
backend/.env
```

示例：

```env
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=

MAP_API_KEY=你的高德Web服务Key
```

说明：

- `MAP_API_KEY` 必须是高德控制台中“Web服务”平台类型的 Key。
- 前端 JS API Key 不能直接当作后端 Web 服务 Key 使用。
- `LLM_API_KEY` 是可选的；如果不配置，后端会使用规则解析兜底。

### 前端配置

文件位置：

```text
frontend/.env
```

示例：

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_AMAP_JS_KEY=你的高德JS API Key
VITE_AMAP_SECURITY_SERVICE_HOST=
```

说明：

- `VITE_API_BASE_URL` 是前端请求后端的地址。
- `VITE_AMAP_JS_KEY` 用于浏览器中加载高德地图。
- 高德前端 JS Key 和后端 Web 服务 Key 是两类 Key。

## 安装和运行

建议使用两个终端分别启动后端和前端。

### 1. 安装并启动后端

```powershell
cd C:\Users\123\Desktop\LLM-Homework\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

后端地址：

```text
http://localhost:8000
```

健康检查：

```text
http://localhost:8000/health
```

### 2. 安装并启动前端

```powershell
cd C:\Users\123\Desktop\LLM-Homework\frontend
npm.cmd install
npm.cmd run dev
```

前端地址：

```text
http://localhost:5173
```

如果你的 PowerShell 不阻止 `npm.ps1`，也可以使用：

```powershell
npm install
npm run dev
```

## 使用方式

1. 启动后端。
2. 启动前端。
3. 打开 `http://localhost:5173`。
4. 允许浏览器定位。
5. 点击示例需求，或手动输入自然语言需求。
6. 点击“生成路线”。

页面会展示：

- 系统理解出的任务链
- 候选地点类型
- 实际选中的 POI
- 路线时间线
- 公交/地铁/打车步骤
- 高德地图路线
- 推荐理由

## 当前示例

前端默认提供三个示例：

```text
到北京南站，顺路吃饭
去五棵松看演唱会，顺便吃饭
去奥森公园跑步，顺便吃饭
```

示例会优先走真实高德服务：

- 搜索中途餐厅
- 规划到目的地的路线
- 展示公交/地铁线路、上车站、下车站、站数和步行段
- 展示打车/驾车备选路线

## 后端接口

### 健康检查

```http
GET /health
```

返回：

```json
{"status": "ok"}
```

### 生成路线方案

```http
POST /api/plans
```

请求示例：

```json
{
  "query": "去奥森公园跑步，顺便吃饭",
  "current_location": {
    "latitude": 39.9042,
    "longitude": 116.4074,
    "accuracy_meters": 30,
    "label": "当前位置"
  }
}
```

返回内容包含：

- `intent`：识别出的目的地、偏好等
- `plans`：候选路线方案
- `pois`：中途地点和终点
- `route`：路线段
- `steps`：公交/地铁/步行详细步骤

### 任务拆解

```http
POST /api/task-plan
```

用于查看系统如何把自然语言拆成任务链和候选 POI 类型。

### 意图解析

```http
POST /api/intent
```

用于单独查看 LLM 或规则解析结果。

### 方案排序

```http
POST /api/rank-plans
```

用于对候选路线按时间、绕路距离、POI 类型匹配和偏好匹配打分排序。

## 高德 Key 配置注意事项

后端 `MAP_API_KEY` 需要使用高德控制台中的“Web服务”Key。

如果填错 Key 类型，常见错误是：

```text
USERKEY_PLAT_NOMATCH
```

这通常表示把“Web端 JS API Key”填到了后端。

正确做法：

```text
backend/.env        -> MAP_API_KEY=高德Web服务Key
frontend/.env       -> VITE_AMAP_JS_KEY=高德Web端JS API Key
```

修改 `.env` 后需要重启对应服务：

- 修改 `backend/.env`：重启后端
- 修改 `frontend/.env`：重启前端

## Mock 兜底机制

如果出现以下情况，后端会自动回退到 mock 方案：

- 没有配置 `MAP_API_KEY`
- 高德请求失败
- 高德没有返回可用 POI
- LLM 不可用或返回非 JSON

mock 模式仍会尽量保留：

- 当前定位
- 明确目的地
- 中途吃饭点
- 交通方式估算

但 mock 方案不代表真实道路、真实公交线路或实时路况。

## 常见问题

### 1. 前端能看到地图，但路线还是 mock

通常是后端 `MAP_API_KEY` 没配置，或者配置成了错误类型的 Key。

检查：

```powershell
cd C:\Users\123\Desktop\LLM-Homework\backend
.\.venv\Scripts\python.exe -c "from app.services.amap_client import amap_is_configured; print(amap_is_configured())"
```

返回 `True` 只表示 Key 存在，不代表 Key 类型一定正确。

### 2. PowerShell 里中文显示乱码

浏览器中文显示依赖 HTML 和源码的 UTF-8 编码。本项目已经设置：

```html
<meta charset="UTF-8" />
<html lang="zh-CN">
```

如果 PowerShell 里看到乱码，多数是终端显示编码问题，不一定是文件损坏。可以用：

```powershell
Get-Content -Encoding UTF8 frontend\src\App.jsx
```

### 3. npm 被 PowerShell 阻止

使用 `npm.cmd`：

```powershell
npm.cmd install
npm.cmd run dev
```

### 4. 前端请求不到后端

确认：

- 后端运行在 `http://localhost:8000`
- `frontend/.env` 中是：

```env
VITE_API_BASE_URL=http://localhost:8000
```

修改后重启前端。

## 当前能力边界

这个项目是课程/演示级 Demo，不是完整生产系统。

当前仍有一些限制：

- 公交/地铁路线来自高德 Web 服务，但没有做实时拥堵、发车间隔和票价展示。
- POI 选择策略还比较简单，主要按中点附近搜索餐厅。
- 目的地识别目前对北京南站、五棵松、奥森公园做了特别优化，更多地点需要继续扩展。
- LLM 意图解析可用但不是必须；没有 LLM 时会使用规则解析。

## 开发原则

- 不要把 API Key 写进代码。
- 不要提交 `.env`。
- 后端和前端保持分离。
- LLM 输出必须校验为 JSON。
- 外部 API 不可用时，优先使用 mock 数据保证 Demo 可复现。
