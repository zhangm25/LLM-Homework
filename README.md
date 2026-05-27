# RoamMind · 随心行 —— 对话式行程规划助手

> 说一句话，我帮你把行程安排好。
>
> 用户用自然语言说出需求（清晰具体的，或模糊带情绪的），助手理解需求、（必要时）追问，
> 给出一条由**真实地点**组成、**时间上跑得通**的行程，在网页上用**地图 + 时间轴**呈现，
> 并支持**一键唤起高德 App 导航**。详见 [`PROPOSAL-0527.md`](./PROPOSAL-0527.md)。

本仓库是按 Proposal 搭好的**可运行实现**：后端走「冷抽取 / 暖表达」的确定性 Prompt 链，
所有外部依赖（DeepSeek、高德）都**可优雅降级**——**不填任何 Key 也能跑起来**，4 个预置场景完整演示。
起点与每个站点都能从**真实候选**里挑选 / 一键替换（确定性重算，不靠模型猜），
非行程或自相矛盾的输入会**主动追问**而非硬编一条假行程。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vite + React 18 + TypeScript（设计 1:1 移植自 `ui-mockup-0527.html`） |
| 地图 | 高德 JS API 2.0（无 Key 时回退到内置 SVG 路线图） |
| 后端 | FastAPI + httpx（异步），SSE 流式 |
| 大模型 | DeepSeek（OpenAI 兼容 SDK），function-calling 工具契约见 `tools/definitions.py` |
| 地图服务 | 高德 搜索 POI 2.0 `/v5/place/*` + 路径规划 2.0 `/v5/direction/*` |

---

## 目录结构

```text
LLM-Homework/
├─ .env.example                # ★ 单一配置模板（复制为 .env 后填 Key）
├─ backend/
│  ├─ requirements.txt
│  ├─ tests/test_roammind.py   # 回归测试（无需 pytest，内置 FakeAMap：python backend/tests/test_roammind.py）
│  └─ app/
│     ├─ main.py               # FastAPI 入口：/api/chat(SSE) /api/route /api/places /api/geocode /api/regeo /api/config /api/health
│     ├─ config.py             # 读取根目录 .env
│     ├─ models/               # Intent Object(§6.2) + Plan/API 契约
│     ├─ llm/                  # DeepSeek 客户端 + P1–P5 Prompt 链 + 受控词表(§6.4)
│     ├─ tools/                # 高德 v5/v2 客户端 + 导航深链 + function-calling 定义(§6.3)
│     ├─ planner/              # 意图 → 落地检索 → 排程 → Plan(§7.3)
│     ├─ agent/                # 编排管线（流式事件）+ 无 Key 时的规则兜底
│     └─ mock/                 # 4 个预置演示场景（NFR-5）
└─ frontend/
   ├─ package.json
   ├─ vite.config.ts          # 开发服把 /api 代理到后端（同源，免开第二个端口）
   └─ src/
      ├─ App.tsx               # 状态编排（含「示例场景=新对话」「换站重规划」）
      ├─ api.ts                # 同源 /api 客户端：SSE 流式 + /api/route 换站 + /api/places 起点候选
      ├─ styles.css            # 移植自 mockup 的设计系统
      ├─ components/           # TopBar(起点候选) / ChatPanel / PlanPanel / MapView / Timeline(每站换一个) …
      └─ lib/amapNav.ts        # 网页 → 唤起高德 App（含微信/桌面兜底，§6.3）
```

---

## 快速开始

### 0. 配置 Key（可选，留空即示例模式）

```bash
cp .env.example .env
# 用编辑器打开 .env，填入 DeepSeek 与高德的 Key（详见文件内注释）
```

> 高德需要**两种** Key（同一控制台分别新建）：后端用「**Web服务**」Key，前端用「**Web端(JS API)**」Key + 安全密钥。
> 任何 Key 留空都不影响启动：缺 DeepSeek 走规则兜底，缺高德走示例数据 / 内置 SVG 地图。

### 1. 后端（conda 环境 `RM`，已预装依赖）

```bash
conda activate RM
# 在仓库根目录运行（--reload-dir backend：只监视后端，避免前端构建触发后端重载）：
uvicorn backend.app.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8000
```

> 若需重建环境：`conda create -n RM python=3.11 -y && conda run -n RM pip install -r backend/requirements.txt`
> **改动后端 `.py` 后需重启 uvicorn** 才会加载新路由（停了再起，或靠 `--reload`）。

### 2. 前端

```bash
cd frontend
npm install        # 首次
npm run dev        # → http://localhost:5173
```

打开 http://localhost:5173 ，点顶部 4 个示例场景任意一个，即可看到完整演示；也可在输入框自由输入。

### 3. 从别的机器访问 / 部署（IP:端口）

前端默认走**同源 `/api`**，所以只需放行**一个端口**：

- **开发（带热更新）**：`npm run dev` 已把 `/api` 代理到后端（见 `vite.config.ts`），
  从 MacBook 用 `http://<服务器IP>:5173` 访问即可，**只需放行 5173**，后端可只监听本机。
- **单端口部署**：`cd frontend && npm run build` 后，FastAPI 会在 `:8000` 同时提供页面与接口，
  打开 `http://<服务器IP>:8000/`，**只需放行 8000**，不需要再跑 Node。

> 若从外网/MacBook 打不开接口（“连接后端失败 / Failed to fetch”），基本都是**云安全组/防火墙没放行那个端口**。

---

## 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 主接口。请求体见 `ChatRequest`，返回 `text/event-stream`：每行 `data: <JSON>`，事件类型 `thinking / understanding / clarify / plan / message / done / error`。 |
| `POST` | `/api/route` | 换站后**确定性重算**路线（不走 LLM、不重检索）：传 `{timeline, city, intent}`，返回更新后的 `Plan`。 |
| `GET` | `/api/places` | 起点候选列表（用户从下拉里挑，而非猜一个）：传 `q` / `city`，返回 `candidates[]`。 |
| `GET` | `/api/geocode` | 把一个地名解析为**单个**坐标（备用）。 |
| `GET` | `/api/regeo` | 浏览器经纬度 → 地址（顶栏定位标签）。 |
| `GET` | `/api/config` | 返回能力开关（`llm_enabled` / `amap_web_enabled` / `default_city`），前端据此显示「示例 / 实时」状态。 |
| `GET` | `/api/health` | 健康检查。 |

数据流（§3 流水线）：

```
用户输入 → P1 意图抽取(LLM，失败/超时降级规则) → 可规划？
   ├─否(非行程/矛盾/空) → 主动澄清(复用 LLM 自带的 clarification_needed)
   └─是 → 「我读懂了你」卡 → 落地检索(高德 POI，每站保留备选)
          → 排程(路径/耗时/时间轴/可行性校验) → P5 共情讲解(流式) → 地图 + 时间轴 + 一键导航
（其后）换某站 → /api/route 确定性重算 ·  换起点 → /api/places 候选选择
```

---

## 已实现 vs 预留

- ✅ 已实现：意图理解（情绪/清晰两类卡片）、**非行程/矛盾/空输入主动澄清**（复用 LLM 自带追问）、
  4 个预置演示场景（点示例卡 = 开新对话）、真实 POI 检索与路径规划接入、
  **起点候选列表选择**、**每站真实备选 + 一键确定性换站重算**、固定日程(会议)锚点排程、
  **真实可行性 / 冲突检测**（迟到固定日程 · 超出结束时间 · 跨午夜 · 跨城/超长距离）、
  **日期与时刻锚点排程**（“明天下午 2 点”按次日 14:00 起排）、
  **反幻觉地点闸门**（虚构地名 → 占位而非乱配，含别名/分店/设施过滤）、
  时间轴排程、地图可视化（高德 JS / SVG 兜底）、流式对话、一键唤起导航（深链 + Web 兜底）、
  **LLM 超时/异常自动降级规则抽取**、全链路无 Key 降级、回归测试 `backend/tests/test_roammind.py`（27 项）。
- 🚧 预留 / 待打磨：**P6 真正的多轮增量重规划**（当前“换站”是其确定性替代，安全但非整句改写）、
  **真实营业时间 open-now 校验**（现仅不再谎称已核对）、整条路线多方案、多城市行程、
  语音实时交互（FR-9）、WGS-84→GCJ-02 坐标转换。

> 设计依据与功能需求编号均对应 [`PROPOSAL-0527.md`](./PROPOSAL-0527.md)。
