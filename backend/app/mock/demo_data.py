"""The four preset demo scenarios, reproduced 1:1 from ui-mockup-0527.html.

Each returns a fully-formed Plan plus the assistant's narration text (streamed
as P5 in mock mode). Coordinates are approximate GCJ-02 for real landmarks, so
the nav links and the real AMap map both work without any backend key.
"""

from __future__ import annotations

from typing import Optional

from ..models.plan import (
    Feasibility,
    NavLinks,
    Plan,
    RouteSegment,
    RouteSummary,
    Stop,
    Understanding,
    UnderstandStep,
)
from ..tools.nav_uri import build_amap_nav_uri

# scenario id -> (matcher keywords). A free-text message hitting >=2 keywords of
# a scenario reuses that polished demo; otherwise the generic pipeline runs.
SCENARIO_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sc1": ("安静", "brunch", "傍晚", "望京", "累"),
    "sc2": ("南锣鼓巷", "烤鸭", "景山", "日落", "故宫"),
    "sc3": ("成都", "喝茶", "盖碗", "夜景", "小吃"),
    "sc4": ("上海中心", "浦东软件园", "开会", "会议", "出差"),
}


def match_scenario(message: str) -> Optional[str]:
    text = (message or "").lower()
    best: Optional[str] = None
    best_hits = 0
    for sc, keywords in SCENARIO_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits > best_hits:
            best_hits = hits
            best = sc
    return best if best_hits >= 2 else None


def _nav(stops: list[Stop]) -> NavLinks:
    """Derive nav links from the located stops (start -> ...vias... -> end)."""
    located = [s for s in stops if s.location]
    if len(located) < 2:
        return NavLinks()
    origin = (located[0].location[0], located[0].location[1], located[0].name)
    dest = (located[-1].location[0], located[-1].location[1], located[-1].name)
    vias = [(s.location[0], s.location[1], s.name) for s in located[1:-1]]
    return build_amap_nav_uri(origin, dest, vias)


def _segments(stops: list[Stop]) -> list[RouteSegment]:
    located_indices = [i for i, s in enumerate(stops) if s.location]
    out: list[RouteSegment] = []
    for a, b in zip(located_indices, located_indices[1:]):
        start, end = stops[a], stops[b]
        out.append(RouteSegment(
            from_index=a,
            to_index=b,
            from_name=start.name,
            to_name=end.name,
            mode="auto",
            polyline=[start.location, end.location] if start.location and end.location else [],
        ))
    return out


# --------------------------------------------------------------------------
# Scenario 1 — Beijing, fuzzy / emotional (the soul demo)
# --------------------------------------------------------------------------
def _sc1() -> tuple[Plan, str]:
    stops = [
        Stop(kind="start", marker="📍", time="现在", name="从当前位置出发",
             location=[116.4610, 39.9088], open_info="📍 国贸附近",
             why="起点默认取你的当前位置，也可在对话里直接说“从 XX 出发”。"),
        Stop(kind="poi", marker="1", time="11:30", name="☕ Voyage 隐巷咖啡 · brunch",
             tags=["慢节奏"], rating=4.7, cost="¥130/人", location=[116.4551, 39.9369],
             why="藏在三里屯巷子里，人少安静、不用排队，正好不赶时间地吃个 brunch。",
             leg="🚗 驾车 12 min · 3.5 km · 停留约 90 min · 营业中 09:00–22:00"),
        Stop(kind="poi", marker="2", time="13:40", name="📖 郎园 Vintage · 院落书店",
             tags=["治愈"], rating=4.6, location=[116.4768, 39.9098],
             why="老厂房改的院子，有书有咖啡，坐下来发会儿呆——你想“静一静”的去处。",
             leg="🚗 驾车 14 min · 4.2 km · 停留约 120 min"),
        Stop(kind="poi", marker="3", time="16:50", name="🌆 亮马河 · 黄昏观景步道",
             tags=["自然 / 水边"], rating=4.8, location=[116.4647, 39.9540],
             why="傍晚沿河散步，看 CBD 天际线在夕阳里亮灯——“看看城市的样子”刚好。",
             leg="🚗 驾车 18 min · 6.0 km · 停留约 70 min"),
        Stop(kind="end", marker="🏠", time="18:30", name="回到望京的家",
             location=[116.4709, 39.9966], why="沿亮马河一路向北，顺路到家，结束放松的一天。",
             leg="🚗 驾车 19 min · 7.5 km"),
    ]
    plan = Plan(
        city="北京", panel_hint="放松向 · 4 站",
        understanding=Understanding(
            kind="mood", title="我想，你需要的是…",
            mood_chips=["有点累", "想静一静"],
            want_chips=["安静", "慢节奏", "治愈", "自然 / 水边"],
            avoid_chips=["嘈杂", "排队", "赶时间"],
        ),
        summary=RouteSummary(total_distance_text="~26 km", total_duration_text="1 h 05 m", stop_count=4,
                             segments=_segments(stops)),
        timeline=stops, nav=_nav(stops),
        feasibility=Feasibility(ok=True, note="已核对营业时间与路程，并按距离选好出行方式 · 行程可行 ✓"),
    )
    narration = ("听起来想给自己放松一天 🍃 我默认从你现在的位置出发。"
                 "给你排了条“慢慢来”的路线：先在三里屯安静吃个 brunch，"
                 "再去院子里的书店待会儿，傍晚到亮马河看城市黄昏，晚上顺路回望京——整条都避开了人挤人的地方。")
    return plan, narration


# --------------------------------------------------------------------------
# Scenario 2 — Beijing, explicit / clear
# --------------------------------------------------------------------------
def _sc2() -> tuple[Plan, str]:
    stops = [
        Stop(kind="start", marker="📍", time="14:00", name="从当前位置出发",
             location=[116.4610, 39.9088], open_info="📍 国贸附近",
             why="你没指定起点，默认取当前位置。"),
        Stop(kind="poi", marker="1", time="14:30", name="🏮 南锣鼓巷",
             tags=["逛"], rating=4.5, location=[116.4030, 39.9376],
             why="老胡同逛吃，按你指定的第一站。",
             leg="🚗 驾车 24 min · 8.6 km · 停留约 110 min"),
        Stop(kind="poi", marker="2", time="16:30", name="🦆 利群烤鸭店（晚饭）",
             tags=["高评分"], rating=4.6, cost="¥110/人", location=[116.4015, 39.8990],
             why="南锣附近评分最高的烤鸭，老店；为赶日落把晚饭提早到这会儿。",
             leg="🚶 步行 9 min · 0.6 km · 停留约 70 min"),
        Stop(kind="poi", marker="3", time="18:40", name="🌅 景山公园 · 万春亭看日落",
             tags=["俯瞰故宫"], rating=4.8, location=[116.3963, 39.9281],
             why="登万春亭俯瞰故宫中轴线，日落约 19:10，提前到刚好占位。",
             leg="🚗 驾车 13 min · 4.1 km · 停留约 70 min · 闭园 21:00"),
        Stop(kind="end", marker="🏠", time="20:05", name="回到望京的家",
             location=[116.4709, 39.9966], why="看完日落直接回家。",
             leg="🚗 驾车 24 min · 11 km"),
    ]
    plan = Plan(
        city="北京", panel_hint="按你的顺序 · 4 站",
        understanding=Understanding(
            kind="explicit", title="我听明白了，你的安排是…",
            steps=[
                UnderstandStep(index="1", label="逛 南锣鼓巷"),
                UnderstandStep(index="2", label="晚饭 · 附近评分高的烤鸭店"),
                UnderstandStep(index="3", label="景山公园 看日落 · 俯瞰故宫"),
                UnderstandStep(index="终", label="回 望京"),
            ],
            constraints=["⏱ 日落前到景山", "🍽 晚饭 = 烤鸭（高评分）", "📍 终点 望京"],
        ),
        summary=RouteSummary(total_distance_text="~15 km", total_duration_text="48 m", stop_count=4,
                             segments=_segments(stops)),
        timeline=stops, nav=_nav(stops),
        feasibility=Feasibility(ok=True, note="已确认顺序与营业时间，并卡住日落时刻 · 行程可行 ✓"),
    )
    narration = ("明白～顺序我都保留了。南锣逛到 16:20 → 利群烤鸭早一点的晚饭 → "
                 "散步到景山赶 19:10 的日落 → 回望京。今天日落约 19:10，我把晚饭稍微提早，"
                 "吃完正好散步去景山占位，时间都卡好了。")
    return plan, narration


# --------------------------------------------------------------------------
# Scenario 3 — Chengdu, generalization to another city
# --------------------------------------------------------------------------
def _sc3() -> tuple[Plan, str]:
    stops = [
        Stop(kind="start", marker="📍", time="现在", name="从当前位置出发",
             location=[104.0817, 30.6517], open_info="📍 春熙路附近",
             why="城市由定位自动识别为成都。"),
        Stop(kind="poi", marker="1", time="13:30", name="🍵 鹤鸣茶社（人民公园）",
             tags=["成都味"], rating=4.7, location=[104.0625, 30.6638],
             why="百年老茶馆，竹椅盖碗茶、掏耳朵，悠闲感拉满。",
             leg="🚗 驾车 11 min · 3.4 km · 停留约 90 min"),
        Stop(kind="poi", marker="2", time="15:20", name="🥟 宽窄巷子 · 逛吃小吃",
             tags=["小吃"], rating=4.5, location=[104.0556, 30.6692],
             why="离人民公园很近，走过去就到——三大炮、钟水饺一路吃。",
             leg="🚶 步行 12 min · 0.9 km · 停留约 80 min"),
        Stop(kind="poi", marker="3", time="18:10", name="🌃 九眼桥 · 锦江夜景",
             tags=["夜景"], rating=4.6, location=[104.0908, 30.6360],
             why="傍晚到，正好等天黑灯亮，沿江看成都夜色。",
             leg="🚗 驾车 16 min · 4.8 km · 停留约 60 min"),
        Stop(kind="end", marker="🏠", time="19:30", name="回到春熙路",
             location=[104.0817, 30.6517], why="看完夜景顺路回去。",
             leg="🚗 驾车 13 min · 3.6 km"),
    ]
    plan = Plan(
        city="成都", panel_hint="成都 · 悠闲向 · 4 站",
        understanding=Understanding(
            kind="mood", title="我想，你需要的是…",
            mood_chips=["想悠闲", "别太累"],
            want_chips=["成都味", "喝茶", "小吃", "慢节奏", "夜景"],
            avoid_chips=["赶时间", "人挤人", "太累"],
        ),
        summary=RouteSummary(total_distance_text="~9 km", total_duration_text="34 m", stop_count=4,
                             segments=_segments(stops)),
        timeline=stops, nav=_nav(stops),
        feasibility=Feasibility(ok=True, note="成都 · 已核对营业与路程，两站很近改为步行 · 行程可行 ✓"),
    )
    narration = ("成都这天气正适合慢慢逛 🍵 安排上了：先去人民公园喝盖碗茶，"
                 "溜达到宽窄巷子吃小吃（很近，走过去就行），傍晚到九眼桥看锦江夜景，再顺路回去。全程不赶。")
    return plan, narration


# --------------------------------------------------------------------------
# Scenario 4 — Shanghai, business trip with fixed-schedule anchors
# --------------------------------------------------------------------------
def _sc4() -> tuple[Plan, str]:
    stops = [
        Stop(kind="fixed", marker="🔒", time="10:00–12:00", name="上海中心 · 客户会议",
             tags=["固定日程"], location=[121.5055, 31.2353],
             why="你定好的硬约束，整条行程围绕它倒排。"),
        Stop(kind="poi", marker="1", time="12:15", name="🍜 陆家嘴 · 快餐午饭",
             tags=["出餐快"], rating=4.5, cost="¥45/人", location=[121.5050, 31.2400],
             why="散会就近解决，评分高、上菜快，不耽误下午。",
             leg="🚶 步行 6 min · 0.4 km · 停留约 40 min"),
        Stop(kind="poi", marker="2", time="13:00", name="☕ 安静咖啡 · 歇脚",
             tags=["安静"], rating=4.6, location=[121.5070, 31.2380],
             why="你要的“安静待半小时”，顺路、人少，正好缓一缓。",
             leg="🚶 步行 4 min · 0.3 km · 停留约 30 min"),
        Stop(kind="fixed", marker="🔒", time="14:00", name="浦东软件园 · 项目会议",
             tags=["固定日程", "13:57 到"], location=[121.6010, 31.2030],
             why="13:35 出发，驾车跨江约 22 min，13:57 到，留 3 min 缓冲。",
             leg="🚗 驾车 22 min · 9.5 km"),
    ]
    plan = Plan(
        city="上海", panel_hint="上海 · 围绕 2 个会 · 4 站",
        understanding=Understanding(
            kind="explicit", title="我听明白了，按你的固定日程排",
            steps=[
                UnderstandStep(index="🔒", label="10:00–12:00 上海中心 · 客户会议", locked=True),
                UnderstandStep(index="1", label="午饭（要快）"),
                UnderstandStep(index="2", label="安静待 30 min"),
                UnderstandStep(index="🔒", label="14:00 浦东软件园 · 项目会议", locked=True),
            ],
            constraints=["🔒 两个会 = 硬约束", "⏱ 会间≈2h，倒排", "🧭 跨江留缓冲"],
        ),
        summary=RouteSummary(total_distance_text="~11 km", total_duration_text="40 m", stop_count=4,
                             segments=_segments(stops)),
        timeline=stops, nav=_nav(stops),
        feasibility=Feasibility(ok=True, note="✓ 两个会已锁定 · 中间不赶 · 时间够"),
    )
    narration = ("两个会我当硬约束锁住了。排好了：12:15 陆家嘴快餐午饭 → 13:00 安静咖啡歇 30 分钟 → "
                 "13:35 出发去浦东软件园，13:57 到，赶得上 2 点的会，还留了几分钟缓冲。")
    return plan, narration


_BUILDERS = {"sc1": _sc1, "sc2": _sc2, "sc3": _sc3, "sc4": _sc4}


def get_demo(scenario: str) -> Optional[tuple[Plan, str]]:
    builder = _BUILDERS.get(scenario)
    return builder() if builder else None
