# RoamMind 系统框图与运行逻辑

本文档用于项目报告说明，重点描述 RoamMind 当前实现中的系统结构、路线确定阶段状态机、Place Resolution 阶段后端与 LLM 的协作，以及行程规划阶段的后端逻辑。

## 1. 系统总览

RoamMind 采用“前端交互 + FastAPI 后端编排 + LLM 语义理解 + 高德地图真实 POI/路线服务”的结构。整体设计思想是先把自然语言需求转成结构化 `IntentObject`，再先确定真实地点，最后进行时空移动规划。

```mermaid
flowchart LR
    U["用户"] --> FE["React 前端"]
    FE -->|"POST /api/chat, SSE"| API["FastAPI 后端"]
    FE -->|"POST /api/files/parse"| FP["附件解析"]
    FE -->|"POST /api/route"| RR["确定性路线重算"]
    FE -->|"GET /api/config"| CFG["运行时配置"]

    API --> PIPE["Agent Pipeline<br/>plan_stream"]
    PIPE --> LLM["LLM 服务<br/>OpenAI-compatible / DeepSeek"]
    PIPE --> PR["Place Resolution<br/>地点优先确定"]
    PR --> MSI["Map Search Intent<br/>搜索意图归一化"]
    MSI --> LLM
    PR --> AMAP["高德 Web 服务<br/>POI / 周边 / 路线 / 逆地理"]
    PIPE --> SCH["Scheduler<br/>行程排程与可行性"]
    SCH --> AMAP
    SCH --> NAV["高德导航链接生成"]

    PIPE -->|"StreamEvent: thinking / clarify / understanding / plan / message / done"| FE
    FE --> UI["候选地点卡片<br/>地图点选<br/>时间轴<br/>导航按钮"]
```

核心数据对象：

| 对象 | 所在文件 | 作用 |
|---|---|---|
| `ChatRequest` | `backend/app/models/plan.py` | 前端发给后端的请求，包含当前消息、历史、城市、当前位置、上轮 Intent、附件解析结果 |
| `IntentObject` | `backend/app/models/intent.py` | LLM/规则抽取得到的结构化需求，包含起终点、任务、固定日程、偏好、澄清状态 |
| `PlaceResolution` | `backend/app/models/place.py` | 地点确定阶段产物，包含每个地点 slot 的候选、默认选择、搜索策略 |
| `Plan` | `backend/app/models/plan.py` | 前端最终渲染的计划，包含时间轴、路线摘要、导航链接、可行性说明 |
| `StreamEvent` | `backend/app/models/plan.py` | SSE 流式事件，驱动前端逐步显示理解、追问、计划和解释 |

## 2. 路线确定阶段的状态机

当前代码中没有单独定义一个 `enum State`，而是由以下变量共同形成“隐式状态机”：

- `ChatRequest.intent`：前端携带的上一轮结构化意图。
- `IntentObject.is_available`：结构体是否足够进入后续规划。
- `IntentObject.pending_question_type` / `pending_field`：上一轮追问正在等待哪个字段。
- `IntentObject.clarification_needed`：LLM 或后端希望向用户提出的问题。
- SSE `StreamEvent.type`：前端看到的状态，如 `clarify`、`plan`、`done`。

### 2.1 总体状态转移

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> ChatRequest: 用户输入/上传附件/点击候选

    ChatRequest --> EmptyCheck
    EmptyCheck --> Clarify: 空输入且无附件
    EmptyCheck --> PresetDemo: scenario 存在
    PresetDemo --> PlanReady: 返回内置 demo plan

    EmptyCheck --> P1Extract: 普通自由文本

    P1Extract --> P1ClarifyRoute: req.intent 存在且 is_available=false
    P1Extract --> P1PatchRoute: req.intent 存在且本轮像修改/补充
    P1Extract --> P1SegmentsRoute: 新需求或完整重抽取

    P1ClarifyRoute --> IntentDraft: P1_CLARIFY 或本地合并用户答案
    P1PatchRoute --> IntentDraft: P1_PATCH
    P1SegmentsRoute --> IntentDraft: P1_SEGMENTS
    IntentDraft --> IntentDraft: 必要时 P1_REPAIR
    IntentDraft --> IntentFallback: LLM 失败/超时/不可用
    IntentFallback --> IntentDraft: heuristic extractor

    IntentDraft --> Clarify: LLM 认为 is_available=false
    IntentDraft --> BackendValidation: is_available=true

    BackendValidation --> ValidateWithLLM: 发现起点/终点/地点等问题
    ValidateWithLLM --> Clarify: LLM 判断需要问用户
    ValidateWithLLM --> IntentPatched: LLM 可直接修补 Intent
    ValidateWithLLM --> IntentValidated: LLM 判断可继续
    BackendValidation --> IntentValidated: 无校验问题
    IntentPatched --> IntentValidated

    IntentValidated --> Clarify: not plannable
    IntentValidated --> Understanding: 可规划
    Understanding --> PlaceResolution
    PlaceResolution --> BuildPlan
    BuildPlan --> PlanReady
    PlanReady --> Narration
    Narration --> Done
    Clarify --> Done
    Done --> Idle
```

### 2.2 关键分支说明

#### A. 新需求抽取

新输入进入 `_extract_intent` 后，如果 LLM 可用，优先使用 `P1_SEGMENTS`。该 prompt 会让 LLM 按原句顺序抽取：

- 起点 `start`
- 终点 `end`
- 行程片段 `segments`
- 固定日程 `fixed_events`
- 情绪/偏好 `mood`、`vibe_tags`、`avoid_tags`
- 是否可进入后续阶段 `is_available`

如果抽取结果有明显语义问题，例如地点字段混入动作、终点误判、返回语义错误，则进入 `P1_REPAIR` 自修复。

#### B. 多轮补全

如果上一轮后端返回了 `clarify`，前端会把未完成的 `IntentObject` 随下一轮用户回答一起发回来。此时 `_is_clarification_turn(req)` 成立，进入 `P1_CLARIFY`：

```mermaid
flowchart TD
    A["上一轮 Intent<br/>is_available=false"] --> B["用户回答问题"]
    B --> C["前端发送 message + prior intent"]
    C --> D{"LLM 可用?"}
    D -->|是| E["P1_CLARIFY<br/>根据 prior intent + 用户回答补全结构体"]
    D -->|否/失败| F["本地规则合并<br/>_merge_clarification_answer"]
    E --> G["新的 IntentObject"]
    F --> G
    G --> H{"is_available=true?"}
    H -->|否| I["继续 clarify"]
    H -->|是| J["进入后端 validation"]
```

这样做的目的，是避免把“用户反馈”粗暴拼接到原需求后面，减少模型把同一地点理解成多个地点的风险。

#### C. 多轮修改

如果前端携带了上一轮可用 Intent，且本轮消息像“调整、换、加一个、顺便、刚才、上一轮”等修改语义，则进入 `P1_PATCH`。后端还会执行 `_merge_patch` 保护逻辑：当 patch 结果把原本多个地点意外压缩成一个地点，或完全丢失原地点集合时，会保留上一轮计划，避免“局部修改变成全局重写”。

#### D. 后端校验与 LLM 裁决

后端 validation 先检查当前 Intent 是否有明显阻塞问题，例如：

- 起点为当前位置但浏览器定位不可用。
- 终点是“宿舍/家/酒店”等泛称，且无法由上下文确定。
- 学校、校区、门等地点可能存在多个候选。

如果有问题，当前实现不是直接由后端固定追问，而是优先把问题列表交给 `P1_VALIDATE`，由 LLM 决定：

```mermaid
flowchart TD
    A["后端 validation issues"] --> B{"LLM 可用?"}
    B -->|否| C["后端 fallback<br/>只对 blocking issue 追问"]
    B -->|是| D["P1_VALIDATE<br/>给 LLM: 原话 + Intent + issues + 位置/历史/附件"]
    D --> E{"LLM action"}
    E -->|"ask_user"| F["返回 clarify<br/>问题 + 选项 + pending intent"]
    E -->|"patch_intent"| G["修补 Intent<br/>继续流程"]
    E -->|"proceed"| H["接受现有 Intent<br/>继续流程"]
    C --> F
```

这对应项目当前的核心原则：后端负责发现结构风险，LLM 负责把风险转译成自然、上下文相关的用户交互。

## 3. Place Resolution 阶段：后端与 LLM 的交互逻辑

Place Resolution 是当前流程里“确定地点”的第一阶段，入口是：

```python
place_resolution = await resolve_place_slots(intent, origin, city)
```

该阶段会生成一组 `PlaceSlot`，每个 slot 对应一个需要落地的地点：

- `start`：起点
- `end`：终点
- `fixed`：固定日程地点
- `waypoint`：明确途径点
- `activity_poi`：由任务/偏好搜索得到的活动地点

每个 slot 包含候选列表 `candidates`、默认选中 `selected`、是否需要用户选择 `needs_user_choice`、搜索范围 `search_scope`、是否周边搜索 `around` 等信息。前端地点卡片和地图点选正是从这里渲染出来的。

### 3.1 Place Resolution 总流程

```mermaid
flowchart TD
    A["IntentObject + origin + city"] --> B["创建 start/end/fixed slots"]
    B --> C{"起点已有坐标?"}
    C -->|有| D["直接 selected<br/>不调用地图搜索"]
    C -->|无，named| E["高德 text search<br/>_resolve_named_slot"]
    C -->|current + origin 可用| D

    B --> F["解析 fixed_events<br/>按名称搜索真实地点"]
    F --> G["构建 grounding targets<br/>explicit_pois + tasks"]

    G --> H["为每个 target 判断上下文<br/>free / between fixed / after fixed"]
    H --> I["确定 previous / next anchor"]
    I --> J{"地点类型"}

    J -->|"明确地点"| K["exact_place<br/>城市/区域 text search<br/>不设 radius"]
    J -->|"命名区域里的活动"| L["in_area<br/>先定位区域，再周边搜索业态"]
    J -->|"附近/顺路/品牌/品类"| M["around_anchor / around_route<br/>围绕上一站/下一站搜索"]
    J -->|"最好/高分/景色好"| N["region_ranked / citywide_ranked<br/>区域内排名搜索"]

    K --> O["候选 POI 列表"]
    L --> O
    M --> O
    N --> O

    O --> P["选择默认候选 candidates[0]"]
    P --> Q["回写 Intent<br/>task.location / explicit.location / endpoint.location"]
    Q --> R["PlaceResolution<br/>slots + candidates + selected"]
```

### 3.2 搜索意图归一化

对于模糊地点、品牌/品类、附近/顺路类需求，后端会调用：

```python
build_map_search_intent(...)
```

它不是直接让 LLM 调地图，而是让 LLM 生成一个受控结构体 `MapSearchIntent`。后端先生成本地 fallback，再在 LLM 可用时请求 LLM 归一化，最后用 `_validate_llm_intent` 做约束校验。

```mermaid
sequenceDiagram
    participant PR as Place Resolution
    participant Local as 本地搜索意图规则
    participant LLM as LLM map_search_intent
    participant Guard as 后端校验/锁定
    participant AMap as 高德 POI API

    PR->>Local: raw_need, task_type, category_hint, mode, anchors
    Local-->>PR: fallback MapSearchIntent
    alt LLM 可用
        PR->>LLM: 最小相关上下文 + 高德搜索规则
        LLM-->>PR: raw MapSearchIntent JSON
        PR->>Guard: 校验 keywords/type_codes/scope/radius
        Guard-->>PR: 安全后的 MapSearchIntent
    else LLM 不可用或失败
        PR->>PR: 使用 fallback
    end
    PR->>AMap: 按 search_scope 选择 text/around/in_area/ranked
    AMap-->>PR: POI candidates
```

`MapSearchIntent` 的关键字段：

| 字段 | 含义 |
|---|---|
| `search_category` | 餐饮、咖啡茶、安静休息、购物、景点等 |
| `search_scope` | `exact_place`、`around_anchor`、`around_route`、`in_area`、`citywide_ranked`、`region_ranked` |
| `anchor_policy` | 是否依赖上一站、下一站、上一站+下一站、区域中心 |
| `ranking_policy` | 相关性、距离、绕路成本、评分、氛围等排序倾向 |
| `keywords` | 直接传入高德搜索的关键词，禁止包含“买点东西/吃饭/去/顺路”等动作短语 |
| `type_codes` | 高德 POI 类型白名单，如餐饮 `050000`、购物 `060000` |
| `radius_m` / `fallback_radius_m` | 周边搜索半径与扩大半径 |

### 3.3 后端对 LLM 的约束

LLM 在 Place Resolution 阶段只负责“语义归一化”，不能直接决定最终地图结果。后端保留以下控制：

1. `type_codes` 只能来自白名单，避免 LLM 生成高德不支持或不合适的类型码。
2. `keywords` 为空时使用本地 fallback。
3. 对品牌/品类/附近/顺路等本地强规则，后端会锁定为 around 搜索，不允许 LLM 改成全城 ranked。
4. 对明确地点 `exact_place`，后端不使用周边搜索，不设置 radius，而是在城市/区域内做文本搜索。
5. 地图 API 结果必须是真实 POI；没有结果时会生成占位并在可行性中提示，而不是让 LLM 幻觉地点。

## 4. 行程规划阶段：后端排程与路线逻辑

Place Resolution 完成后，`IntentObject` 已经尽可能带有真实坐标。接下来进入：

```python
plan = await build_plan(intent, origin, city)
plan.place_resolution = place_resolution
```

行程规划阶段的职责不是重新理解用户意图，而是把已经确定的地点变成时间轴和路线。

### 4.1 `build_plan` 主流程

```mermaid
flowchart TD
    A["IntentObject<br/>已被 Place Resolution 回写坐标"] --> B["确定日期与起始时间<br/>date / time_window / task time_hint"]
    B --> C["确定起点<br/>named start / browser origin / city center fallback"]
    C --> D["解析固定日程 fixed_events<br/>按时间排序，作为硬锚点"]
    D --> E["生成 grounding targets<br/>任务和途径点"]
    E --> F["将 target 转成 Stop"]
    F --> G{"task.location 已存在?"}
    G -->|是| H["直接使用地点确定阶段结果"]
    G -->|否| I["兜底搜索<br/>named / in_area / near previous"]
    H --> J["组装 PlanItem"]
    I --> J
    J --> K{"存在固定日程?"}
    K -->|是| L["固定日程切分时间窗口<br/>把任务插入会前/会间/会后"]
    K -->|否| M["按 start -> tasks -> end 顺序组装"]
    L --> N["_finalize"]
    M --> N
```

### 4.2 固定日程处理

固定日程是硬约束。调度器会：

1. 按 `fixed_event.start` 排序。
2. 将固定日程地点转成 `Stop(kind="fixed")`。
3. 对两个固定日程之间的任务，调用 `_task_likely_between_fixed` 判断是否应插入会间。
4. 对第一个固定日程，如果用户给了明确起点，会反推起点出发时间，预留 `FIRST_FIXED_BUFFER_MIN` 缓冲。
5. 如果预计到达固定日程时已经迟到，会把问题写入 `feasibility.note`。

### 4.3 `_finalize` 路线与时间级联

`_finalize` 是初次规划和换地点重算共用的确定性函数。

```mermaid
flowchart TD
    A["PlanItem 列表"] --> B["初始化 clock"]
    B --> C["遍历每个 Stop"]
    C --> D{"前后 Stop 都有坐标?"}
    D -->|是| E["调用高德路线<br/>短距离步行，长距离驾车"]
    D -->|否| F["示例耗时 fallback"]
    E --> G["累加距离/耗时/polyline/route_segments"]
    F --> G
    G --> H{"到达固定日程?"}
    H -->|是| I["检查是否迟到<br/>计算缓冲或记录 issue"]
    H -->|否| J["应用 time_hint 下限<br/>计算停留 dwell"]
    I --> K["写入 stop.time / stop.leg"]
    J --> K
    K --> L{"还有 Stop?"}
    L -->|是| C
    L -->|否| M["日级可行性检查<br/>跨午夜/超时/跨城"]
    M --> N["生成 RouteSummary"]
    N --> O["生成高德导航链接"]
    O --> P["返回 Plan"]
```

可行性检查包括：

- 是否跨越午夜。
- 是否超过用户希望的结束时间。
- 是否出现单段超过约 120km 的路线，提示疑似跨城市。
- 是否存在未找到真实地点的占位。
- 是否赶不上固定日程。

当前实现不会真正验证营业时间，因此可行性文案不会声称“已核对营业时间”。

### 4.4 换地点后的重算

前端地点卡片或时间轴中切换候选后，不会重新进入 LLM，也不会重新做 Place Resolution。流程是：

```mermaid
sequenceDiagram
    participant FE as 前端
    participant API as /api/route
    participant SCH as recompute_plan
    participant AMap as 高德路线 API

    FE->>FE: 用户选择某个候选 POI
    FE->>FE: 替换 timeline 中对应 Stop 的 name/location
    FE->>API: POST /api/route { timeline, city, intent }
    API->>SCH: recompute_plan
    SCH->>AMap: 重新计算相邻 Stop 的路线和耗时
    SCH-->>API: 新 Plan
    API-->>FE: 更新时间轴、地图、导航链接
```

这保证了“换一个地点”是快速、可解释、确定性的：只改变用户选中的 POI 及其连带路线耗时，不让模型重新解释整段需求。

## 5. 端到端运行逻辑摘要

```mermaid
flowchart TD
    A["用户自然语言/附件"] --> B["前端构造 ChatRequest<br/>message/history/origin/intent/file_contexts"]
    B --> C["后端 plan_stream"]
    C --> D["P1 抽取/补全/修改 Intent"]
    D --> E{"Intent 可用?"}
    E -->|否| F["SSE clarify<br/>前端展示问题"]
    F --> A
    E -->|是| G["后端 validation"]
    G --> H{"需要用户参与?"}
    H -->|是| F
    H -->|否| I["understanding 事件<br/>展示理解卡"]
    I --> J["Place Resolution<br/>确定真实地点和候选"]
    J --> K["Scheduler<br/>计算路线和时间轴"]
    K --> L["plan 事件<br/>地图 + 地点卡片 + 时间轴"]
    L --> M["P5 warm narration<br/>流式解释"]
    M --> N["done"]
    L --> O["用户换候选地点"]
    O --> P["/api/route 确定性重算"]
    P --> L
```

一句话概括当前系统逻辑：RoamMind 不是让 LLM 一步生成完整路线，而是让 LLM 负责语义理解和必要澄清，让后端控制地点落地、地图检索、路线计算、时间排程和可行性判断，从而在自然交互与真实可执行之间取得平衡。

## 6. 可在报告中强调的设计特点

1. **结构化中间层**：自然语言不会直接变成路线，而是先变成 `IntentObject`，便于校验、补全和多轮修改。
2. **地点优先**：先通过 Place Resolution 把起点、终点、固定日程和活动地点落到真实 POI，再做路线规划。
3. **LLM 受控参与**：LLM 负责理解、澄清和搜索意图归一化；地图调用、候选选择、半径策略、可行性判断由后端掌控。
4. **候选可交互**：每个地点 slot 都可以保留多个候选，前端允许用户点选候选并触发确定性重算。
5. **渐进降级**：LLM 不可用时走 heuristic；高德不可用时使用示例/占位；流式接口不会因为单个外部服务失败而挂死。
6. **多轮状态保持**：前端把上一轮 `IntentObject` 发回后端，使澄清回答和局部修改可以在结构体层面继续，而不是简单拼接文本。
