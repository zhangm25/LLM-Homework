# RoamMind · 随心行 —— 对话式行程规划助手

> 说一句话，我帮你把行程安排好。
>
> 用户用自然语言说出需求（清晰具体的，或模糊带情绪的），助手理解需求、（必要时）追问，
> 给出一条由**真实地点**组成、**时间上跑得通**的行程，在网页上用**地图 + 时间轴**呈现，
> 并支持**一键唤起高德 App 导航**。详见 [`PROPOSAL-0527.md`](./PROPOSAL-0527.md)。

本仓库是按 Proposal 搭好的**可运行代码框架**：后端走「冷抽取 / 暖表达」的确定性 Prompt 链，
所有外部依赖（DeepSeek、高德）都**可优雅降级**——**不填任何 Key 也能跑起来**，4 个预置场景完整演示。

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
│  └─ app/
│     ├─ main.py               # FastAPI 入口：/api/chat (SSE) /api/config /api/health
│     ├─ config.py             # 读取根目录 .env
│     ├─ models/               # Intent Object(§6.2) + Plan/API 契约
│     ├─ llm/                  # DeepSeek 客户端 + P1–P5 Prompt 链 + 受控词表(§6.4)
│     ├─ tools/                # 高德 v5/v2 客户端 + 导航深链 + function-calling 定义(§6.3)
│     ├─ planner/              # 意图 → 落地检索 → 排程 → Plan(§7.3)
│     ├─ agent/                # 编排管线（流式事件）+ 无 Key 时的规则兜底
│     └─ mock/                 # 4 个预置演示场景（NFR-5）
└─ frontend/
   ├─ package.json
   └─ src/
      ├─ App.tsx               # 状态编排
      ├─ api.ts                # fetch 流式（SSE）客户端
      ├─ styles.css            # 移植自 mockup 的设计系统
      ├─ components/           # TopBar / ChatPanel / PlanPanel / MapView / Timeline …
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
# 在仓库根目录运行：
uvicorn backend.app.main:app --reload --port 8000
```

> 若需重建环境：`conda create -n RM python=3.11 -y && conda run -n RM pip install -r backend/requirements.txt`

### 2. 前端

```bash
cd frontend
npm install        # 首次
npm run dev        # → http://localhost:5173
```

打开 http://localhost:5173 ，点顶部 4 个示例场景任意一个，即可看到完整演示；也可在输入框自由输入。

---

## 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 主接口。请求体见 `ChatRequest`，返回 `text/event-stream`：每行 `data: <JSON>`，事件类型 `thinking / understanding / plan / message / done / error`。 |
| `GET` | `/api/config` | 返回能力开关（`llm_enabled` / `amap_web_enabled` / `default_city`），前端据此显示「示例 / 实时」状态。 |
| `GET` | `/api/health` | 健康检查。 |

数据流（§3 流水线）：

```
用户输入 → P1 意图抽取(LLM 或规则) → 「我读懂了你」卡 → 落地检索(高德 POI)
        → 排程(路径/耗时/时间轴) → P5 共情讲解(流式) → 地图 + 时间轴 + 一键导航
```

---

## 已实现 vs 预留

- ✅ 已实现：意图理解（情绪/清晰两类卡片）、4 个预置演示场景、真实 POI 检索与路径规划接入、
  时间轴排程、地图可视化（高德 JS / SVG 兜底）、流式对话、一键唤起导航（深链 + Web 兜底）、
  全链路无 Key 降级。
- 🚧 框架预留（已留接缝，待打磨）：多候选「广搜→组合→择优」、围绕任意固定日程的严格倒排、
  多轮增量重规划的 Intent patch（P6）、语音实时交互（FR-9）、WGS-84→GCJ-02 坐标转换。

> 设计依据与功能需求编号均对应 [`PROPOSAL-0527.md`](./PROPOSAL-0527.md)。
