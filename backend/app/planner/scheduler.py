"""Live scheduler: Intent Object -> timed Plan.

Pragmatic single-route version of §7.3 (start -> stops -> end, fixed events as
front/back anchors). Key behaviours:
  * the named start / end ("我现在在清华大学", "回望京") are GEOCODED, not hardcoded;
  * each stop's search query is built by MERGING the named place (explicit_pois)
    with the activity (tasks) — e.g. place "五道口" + task "吃顺德菜" -> "五道口 顺德菜",
    place "玉渊潭公园" -> "玉渊潭公园" — so we ground the user's real words, not a
    generic type keyword; among the top weight-ranked hits we pick the one nearest
    the previous stop so the route stays sane;
  * real per-leg route polylines are collected into summary.polyline so the
    frontend draws actual roads, not straight lines.
When AMap is off it emits clearly-labelled 示例 stops so the shape still renders.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Optional

from ..models.intent import IntentObject, Task
from ..models.plan import (
    Feasibility,
    NavLinks,
    Plan,
    POIChoice,
    RouteSegment,
    RouteSummary,
    Stop,
    Understanding,
    UnderstandStep,
)
from ..tools.amap_client import POI, Leg, get_amap
from ..tools.nav_uri import build_amap_nav_uri
from ..llm.vocab import TASK_DEFAULT_KEYWORD
from ..agent.place_norm import normalize_place_name

CITY_CENTER: dict[str, list[float]] = {
    "北京": [116.4074, 39.9042],
    "上海": [121.4737, 31.2304],
    "成都": [104.0668, 30.5728],
    "广州": [113.2644, 23.1291],
    "深圳": [114.0579, 22.5431],
    "杭州": [120.1551, 30.2741],
}
DEFAULT_CITY = "北京"
DRIVE_WALK_THRESHOLD_M = 1000  # below this, "auto" picks walking
CANDIDATE_POOL = 8  # consider the top-N weight-ranked hits, then pick nearest
DEFAULT_DWELL_MIN = 70
FIRST_FIXED_BUFFER_MIN = 20

# Leading verbs we strip so "吃顺德菜" -> "顺德菜", "逛逛" -> "" (place wins instead).
_VERB_PREFIXES = ["吃个", "吃点", "喝个", "喝点", "逛个", "逛逛", "找个", "看看", "去", "到", "逛", "吃", "喝", "看", "玩", "找"]
_GENERIC_FOOD = {"", "饭", "东西", "饭菜", "点东西", "个饭"}
_GENERIC_DINING_INTENTS = _GENERIC_FOOD | {"吃饭", "午饭", "晚饭", "餐厅"}
# Category tokens that mark a task as "a venue you go INTO" (search the category
# in the area) rather than a destination (the named place itself).
_CATEGORY_TOKENS = (
    "餐厅", "美食", "菜", "火锅", "烤", "咖啡", "茶", "奶茶", "酒吧", "书店", "商场",
    "购物", "电影", "健身", "面", "小吃", "brunch", "西餐", "日料", "烧烤", "甜品", "酒馆",
)
_MAIN_POI_TOKENS = ("购物中心", "商场", "公寓", "大楼", "楼", "大学", "学院", "公园", "医院", "酒店", "地铁站")
_SUB_POI_TOKENS = ("火锅", "餐厅", "美食", "烤", "咖啡", "茶", "小吃", "店")

Target = tuple[Optional[str], Optional[Task]]  # (named place, activity)


def _haversine_m(a: list[float], b: list[float]) -> float:
    r = 6_371_000
    lng1, lat1, lng2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _parse_clock(text: Optional[str], base: datetime) -> datetime:
    if text and ":" in text:
        try:
            hh, mm = text.split(":", 1)
            return base.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except ValueError:
            pass
    return base


def _clean_intent(intent: str) -> str:
    s = (intent or "").strip()
    for verb in _VERB_PREFIXES:
        if s.startswith(verb):
            s = s[len(verb):]
            break
    return s.strip(" 的个家")


def _venue_category(task: Optional[Task]) -> Optional[str]:
    """If the task is "go into a venue" (eat / coffee / shop), return the
    category to search (cuisine or fallback); else None — meaning the named
    place itself is the destination (park / campus / art district)."""
    if task is None:
        return None
    kw = _clean_intent(task.intent or "")
    if task.type == "dining":
        return kw if kw not in _GENERIC_FOOD else "餐厅"
    if task.type == "shopping":
        return kw or "商场"
    if kw and any(tok in kw for tok in _CATEGORY_TOKENS):
        return kw  # leisure with an explicit category, e.g. "咖啡" / "书店"
    return None


def _place_should_be_main_destination(place: Optional[str]) -> bool:
    return bool(place and any(tok in place for tok in _MAIN_POI_TOKENS))


def _same_named_place(a: Optional[str], b: Optional[str]) -> bool:
    x = normalize_place_name(a)
    y = normalize_place_name(b)
    if not x or not y:
        return False
    return x == y or x in y or y in x


def _reserved_anchor_places(intent: IntentObject) -> list[str]:
    out = [ev.place for ev in intent.fixed_events if ev.place]
    if intent.constraints.start.type == "named" and intent.constraints.start.value:
        out.append(intent.constraints.start.value)
    if intent.constraints.end and intent.constraints.end.value:
        out.append(intent.constraints.end.value)
    return [normalize_place_name(p) or p for p in out if p]


def _is_reserved_anchor(place: Optional[str], anchors: list[str]) -> bool:
    return bool(place and any(_same_named_place(place, anchor) for anchor in anchors))


def _grounding_targets(intent: IntentObject) -> list[Target]:
    """Merge named places with activities while avoiding obvious mis-pairings.

    Generic dining slots ("吃饭") often mean "find a restaurant near the route",
    not "the next named landmark is a restaurant". Keep those as place-less
    targets when a later explicit activity still needs the next named place.
    """
    reserved = _reserved_anchor_places(intent)
    explicit = [
        p for p in sorted(
            intent.explicit_pois, key=lambda p: p.fixed_order_index if p.fixed_order_index is not None else 999
        )
        if not _is_reserved_anchor(p.name, reserved)
    ]
    for poi in explicit:
        poi.name = normalize_place_name(poi.name) or poi.name
    tasks = [
        t for t in intent.tasks
        if t.needs_poi
        and t.type != "meeting"
        and not _is_reserved_anchor(t.at, reserved)
    ]
    out: list[Target] = []
    place_i = 0
    used_places: set[int] = set()
    for i, task in enumerate(tasks):
        place: Optional[str] = None
        if task.at:
            task.at = normalize_place_name(task.at) or task.at
            for j, poi in enumerate(explicit):
                if j not in used_places and poi.name == task.at:
                    place = explicit[j].name
                    used_places.add(j)
                    break
            out.append((place, task))
            continue
        if task.explicit and not task.at and len(tasks) > len(explicit):
            out.append((None, task))
            continue
        later_non_dining = any(t.type != "dining" for t in tasks[i + 1 :])
        has_future_place = place_i < len(explicit)
        generic_dining = task.type == "dining" and _clean_intent(task.intent) in _GENERIC_DINING_INTENTS
        if not (generic_dining and later_non_dining and has_future_place):
            while place_i < len(explicit) and place_i in used_places:
                place_i += 1
            if place_i < len(explicit):
                place = explicit[place_i].name
                used_places.add(place_i)
                place_i += 1
        out.append((place, task))

    for i, p in enumerate(explicit):
        if i not in used_places:
            out.append((p.name, None))
    return out


def _target_label(place: Optional[str], task: Optional[Task]) -> str:
    if place and task and task.type == "pickup":
        return f"接人 · {place}"
    if place and task and task.type == "dining" and _clean_intent(task.intent or "") not in _GENERIC_FOOD:
        return f"{place} · {task.intent}"
    if place:
        return place
    return (task.intent if task else "地点") or "地点"


def _split_area_hint(place: str) -> tuple[Optional[str], str]:
    if "附近的" in place:
        area, target = place.split("附近的", 1)
        return area.strip() or None, target.strip() or place
    if "附近" in place:
        area, target = place.split("附近", 1)
        target = target.strip("的 ")
        if target:
            return area.strip() or None, target
    return None, place


def build_understanding(intent: IntentObject) -> Understanding:
    if intent.is_explicit:
        steps: list[UnderstandStep] = []
        for ev in intent.fixed_events:
            steps.append(UnderstandStep(index="🔒", label=f"{ev.start} {ev.place} · {ev.title}", locked=True))
        for i, (place, task) in enumerate(_grounding_targets(intent), start=1):
            steps.append(UnderstandStep(index=str(i), label=_target_label(place, task)))
        if intent.constraints.end and intent.constraints.end.value:
            steps.append(UnderstandStep(index="终", label=f"回 {intent.constraints.end.value}"))
        constraints = []
        if intent.constraints.start.type == "named" and intent.constraints.start.value:
            constraints.append(f"📍 起点 {intent.constraints.start.value}")
        if intent.constraints.end and intent.constraints.end.value:
            constraints.append(f"🏁 终点 {intent.constraints.end.value}")
        if intent.fixed_events:
            constraints.append("🔒 固定日程为硬约束")
        return Understanding(kind="explicit", title="我听明白了，你的安排是…", steps=steps, constraints=constraints)

    prefs = intent.implicit_preferences
    return Understanding(
        kind="mood",
        title="我想，你需要的是…",
        mood_chips=[prefs.mood] if prefs.mood else [],
        want_chips=prefs.vibe_tags or prefs.desired_categories,
        avoid_chips=prefs.avoid_tags,
    )


async def _geocode(query: Optional[str], city: str) -> Optional[list[float]]:
    """Resolve a named place to coordinates via AMap text search."""
    amap = get_amap()
    if not amap.enabled or not query:
        return None
    pois = await amap.search_poi_text(query, region=city, page_size=1)
    return pois[0].location if pois else None


def _base_name(name: str) -> str:
    """Strip a trailing branch suffix: "新辰里购物中心(亚运村店)" -> "新辰里购物中心".
    Lets us match a mall by its real name, and stops the "店" inside a branch
    suffix from being mistaken for a sub-shop by _SUB_POI_TOKENS."""
    s = (name or "").strip()
    for lp, rp in (("(", ")"), ("（", "）")):
        if s.endswith(rp):
            i = s.rfind(lp)
            if i > 0:
                return s[:i].strip()
    return s


def _name_related(query: str, name: str) -> bool:
    """Guard against AMap fuzzily returning an unrelated POI for a name that
    doesn't really exist — the "霍格沃茨魔法学校 -> 明月魔法学院" / "北科大南门 ->
    南门涮肉" trap that quietly breaks the anti-hallucination promise. Require the
    resolved name to share the query's distinctive head, or one to contain the
    other. Only used for *named destinations*, never for category searches
    (we *want* any good "火锅"/"咖啡馆" there)."""
    q = _base_name(query)
    if not q:
        return False
    for n in (name, _base_name(name)):
        if not n:
            continue
        if q in n or n in q:
            return True
        if len(q) >= 2 and q[:2] in n:  # distinctive leading token (e.g. 玉渊/国家)
            return True
    return False


# Famous landmarks often have an official name that shares no characters with
# their colloquial one (鸟巢 -> 国家体育场, 水立方 -> 国家游泳中心). When AMap returns a
# major civic POI we trust its alias resolution and skip the name check; a
# fictional "明月魔法学院" (休闲场所) gets no such pass.
_LANDMARK_TYPES = (
    "风景名胜", "运动场馆", "旅游景点", "文物古迹", "博物馆", "纪念馆",
    "公园", "植物园", "动物园", "体育场馆", "教堂", "寺庙", "高等院校",
)
# Facility POIs we never want as a *destination* when a real one is available.
_FACILITY_HINT = ("停车场", "出入口", "收费站", "厕所", "卫生间", "检票口", "售票")


def _is_landmark(poi: POI) -> bool:
    return any(t in poi.type for t in _LANDMARK_TYPES)


def _is_facility(poi: POI) -> bool:
    return ("停车场" in poi.type) or any(h in poi.name for h in _FACILITY_HINT)


def _rank(cands: list[POI]) -> list[POI]:
    """Order candidates best-first: rated (highest first) ahead of unrated,
    facilities dropped. cands[0] is the pick; cands[1:] are the alternatives."""
    usable = [p for p in cands if not _is_facility(p)] or cands
    pool = usable[:CANDIDATE_POOL]
    rated = sorted((p for p in pool if p.rating), key=lambda p: p.rating or 0, reverse=True)
    unrated = [p for p in pool if not p.rating]
    return rated + unrated + usable[CANDIDATE_POOL:]


def _pick_best(cands: list[POI]) -> Optional[POI]:
    ranked = _rank(cands)
    return ranked[0] if ranked else None


async def _resolve_named(place: str, city: str) -> list[POI]:
    """A named destination (park / campus / district): the main POI first, then
    other matches as alternatives. Empty if nothing plausibly matches."""
    amap = get_amap()
    if not amap.enabled:
        return []
    area, target = _split_area_hint(place)
    cands: list[POI] = []
    if area:
        # A *named* destination with an area hint ("六道口附近的新辰里购物中心"): a
        # combined text search pins the right branch, whereas a radius sweep
        # tends to surface sub-shops and parking entrances around it.
        cands = await amap.search_poi_text(f"{area} {target}", region=city, page_size=20)
        if not cands:
            center = await _geocode(area, city)
            if center:
                cands = await amap.search_poi_around(target, center, radius_m=2500, sortrule="weight", page_size=20)
    if not cands:
        cands = await amap.search_poi_text(target, region=city, page_size=20)
    main = _pick_main_poi(target, cands)
    # Anti-hallucination: if the best hit isn't related to what the user named
    # (and isn't a trusted civic landmark resolved by alias), don't pretend —
    # let build_plan fall back to a labelled placeholder rather than route them
    # to an unrelated POI miles away.
    if not main or (not _name_related(target, main.name) and not _is_landmark(main)):
        return []
    others = [c for c in cands if c.location != main.location and not _is_facility(c)]
    return [main, *others]


def _pick_main_poi(query: str, cands: list[POI]) -> Optional[POI]:
    if not cands:
        return None
    cands = [p for p in cands if not _is_facility(p)] or cands
    wants_main = any(tok in query for tok in _MAIN_POI_TOKENS)
    if not wants_main:
        return cands[0]
    # Match on the branch-stripped name so "新辰里购物中心(亚运村店)" matches a
    # query of "新辰里购物中心" (and isn't dropped as if "店" made it a sub-shop).
    exact = [p for p in cands if _base_name(p.name) == _base_name(query)]
    if exact:
        return exact[0]
    mainish = [
        p for p in cands
        if any(tok in p.name for tok in _MAIN_POI_TOKENS)
        and not any(tok in _base_name(p.name) for tok in _SUB_POI_TOKENS)
    ]
    if mainish:
        return mainish[0]
    no_sub = [p for p in cands if not any(tok in _base_name(p.name) for tok in _SUB_POI_TOKENS)]
    return no_sub[0] if no_sub else cands[0]


async def _resolve_in_area(place: str, category: str, city: str) -> list[POI]:
    """A venue (restaurant / cafe / shop) inside a named area: search the
    category in that area; best-rated first, the rest as alternatives."""
    amap = get_amap()
    if not amap.enabled:
        return []
    cands = await amap.search_poi_text(f"{place} {category}", region=city, page_size=15)
    if not cands:  # fall back to a radius search around the area centre
        area = await _geocode(place, city)
        if area:
            cands = await amap.search_poi_around(category, area, radius_m=2000, sortrule="weight", page_size=15)
    return _rank(cands)


async def _resolve_near(query: str, prev_loc: list[float], city: str) -> list[POI]:
    """A vague activity with no named area: search near the previous stop,
    best-rated first; fall back to a city-wide text search."""
    amap = get_amap()
    if not amap.enabled:
        return []
    cands = await amap.search_poi_around(query, prev_loc, radius_m=8000, sortrule="weight", page_size=15)
    if not cands:
        cands = await amap.search_poi_text(query, region=city, page_size=15)
    return _rank(cands)


async def _leg_for(a: list[float], b: list[float]) -> tuple[Optional[Leg], str]:
    """Return (leg, human text). Picks walking vs driving by distance ("auto")."""
    amap = get_amap()
    straight = _haversine_m(a, b)
    if not amap.enabled:
        mode = "🚶 步行" if straight < DRIVE_WALK_THRESHOLD_M else "🚗 驾车"
        return None, f"{mode} · 示例耗时 · 出行方式按距离自动选"
    if straight < DRIVE_WALK_THRESHOLD_M:
        leg = await amap.route_walking(a, b)
        icon = "🚶 步行"
    else:
        leg = await amap.route_driving(a, b)
        icon = "🚗 驾车"
    if leg is None:
        return None, f"{icon} · 出行方式按距离自动选"
    mins = max(1, round(leg.duration_s / 60))
    km = round(leg.distance_m / 1000, 1)
    return leg, f"{icon} {mins} min · {km} km"


def _extract_hhmm(text: Optional[str]) -> Optional[str]:
    """Pull a clock time out of a free-form hint: ">=11:00" / "13:00前" -> "11:00"."""
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mn:02d}" if 0 <= h <= 23 and 0 <= mn <= 59 else None


def _date_offset(date_str: Optional[str]) -> int:
    """Days from today implied by intent.date ('明天'/'后天'/ISO). The whole
    timeline anchors to this date so "明天10点开会" plans for tomorrow, not now."""
    s = (date_str or "").strip()
    if "大后天" in s:
        return 3
    if "后天" in s:
        return 2
    if "明天" in s or "明日" in s or "tomorrow" in s.lower():
        return 1
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            target = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            return max(0, (target - datetime.now().date()).days)
        except ValueError:
            pass
    return 0


def _earliest_anchor(intent: IntentObject) -> Optional[str]:
    """Earliest stated clock time across the time window and task hints — used
    to start the day at the time the user actually named, not at 'now'."""
    cands = [h for h in [_extract_hhmm(intent.constraints.time_window.start)] if h]
    cands += [h for t in intent.tasks if (h := _extract_hhmm(t.time_hint))]
    return min(cands) if cands else None


def _fixed_label(meta: Optional[dict]) -> str:
    if not meta or not meta.get("start"):
        return ""
    s = meta["start"].strftime("%H:%M")
    return f"{s}–{meta['end'].strftime('%H:%M')}" if meta.get("end") else s


def _advance_after(clock: datetime, meta: Optional[dict], dwell: int) -> datetime:
    """Clock right after finishing a stop: a fixed event ends at its end time
    (or start + a default block); a normal stop ends after its dwell."""
    if meta and meta.get("start"):
        return meta["end"] or (meta["start"] + timedelta(minutes=dwell or 60))
    return clock + timedelta(minutes=dwell or 0)


def _gap_clock(meta: dict, key: str) -> Optional[datetime]:
    return meta.get(key) or (meta.get("start") if key == "end" else None)


def _gap_spans_lunch(prev_meta: dict, next_meta: dict) -> bool:
    start = _gap_clock(prev_meta, "end")
    end = _gap_clock(next_meta, "start")
    if not start or not end:
        return False
    lunch_start = start.replace(hour=11, minute=0, second=0, microsecond=0)
    lunch_end = start.replace(hour=14, minute=0, second=0, microsecond=0)
    return start <= lunch_end and end >= lunch_start


def _task_likely_between_fixed(task: Optional[Task], prev_meta: dict, next_meta: dict) -> bool:
    if task is None:
        return False
    text = task.intent or ""
    hint = _extract_hhmm(task.time_hint)
    if hint:
        hint_dt = _parse_clock(hint, _gap_clock(prev_meta, "end") or datetime.now())
        start = _gap_clock(prev_meta, "end") or _gap_clock(prev_meta, "start")
        end = _gap_clock(next_meta, "start")
        return bool(start and end and start <= hint_dt <= end)
    if task.type == "dining" and (_gap_spans_lunch(prev_meta, next_meta) or any(k in text for k in ("午饭", "午餐", "中午"))):
        return True
    if any(k in text for k in ("会间", "中间", "安静", "待", "休息", "咖啡", "半小时")):
        return True
    return False


async def _resolve_stop(
    place: Optional[str], task: Optional[Task], prev_loc: list[float],
    city: str, marker: str, amap_on: bool,
) -> tuple[Stop, int]:
    """Ground one (place, task) target into a real Stop plus its dwell minutes."""
    category = _venue_category(task)
    if place and category and not _place_should_be_main_destination(place):  # venue inside a named area
        cands = await _resolve_in_area(place, category, city)
        label = f"{place} {category}"
        why = f"你想在「{place}」{task.intent if task else ''}，给你挑了评分高的这家。"
    elif place:  # the named place itself is the destination
        cands = await _resolve_named(place, city)
        label = place
        why = f"到「{place}」接人。" if task and task.type == "pickup" else f"你点名要去的「{place}」。"
    else:  # vague activity, no named place -> search near the previous stop
        label = (
            category
            or (_clean_intent(task.intent) if task and task.intent else "")
            or TASK_DEFAULT_KEYWORD.get(task.type if task else "other", "地点")
        )
        cands = await _resolve_near(label, prev_loc, city)
        why = f"按你说的「{label}」给你找的。"
    dwell = task.dwell_min if task else DEFAULT_DWELL_MIN
    if cands:
        chosen = cands[0]
        alternatives = [
            POIChoice(name=p.name, location=p.location, rating=p.rating, cost=p.cost, address=p.address)
            for p in cands[1:4]
        ]
        return Stop(
            kind="poi", marker=marker, time="", name=chosen.name, tags=[],
            rating=chosen.rating, cost=chosen.cost, location=chosen.location,
            open_info=chosen.opentime_today, why=why, dwell_min=dwell, alternatives=alternatives,
        ), dwell
    placeholder_why = (
        f"没找到和「{label}」匹配的真实地点，先用示例占位；可以换个更具体的名字。"
        if amap_on
        else "高德未配置，这里用示例占位；配置 Key 后会替换为真实检索结果。"
    )
    return Stop(
        kind="poi", marker=marker, time="", name=f"{label}（示例）",
        tags=[], location=None, why=placeholder_why, dwell_min=dwell,
    ), dwell


# An ordered timeline item: (stop, dwell_min, fixed_meta|None, time_floor|None).
PlanItem = tuple[Stop, int, Optional[dict], Optional[str]]


async def build_plan(
    intent: IntentObject,
    origin: Optional[list[float]],
    city: str,
    source: str = "live",
) -> Plan:
    amap = get_amap()
    # Anchor the whole timeline to the requested day, starting at the earliest
    # time the user actually named (not "now"); a future day with no time given
    # defaults to a 09:00 morning start.
    offset = _date_offset(intent.date)
    base = datetime.now() + timedelta(days=offset)
    start_anchor = _earliest_anchor(intent)
    start_clock_src = start_anchor or (None if offset == 0 else "09:00") or intent.constraints.time_window.start

    # --- start: honour a named start, else geo fix, else city centre -----
    start = intent.constraints.start
    start_named = start.type == "named" and bool(start.value)
    start_loc = origin
    if start_named:
        start_loc = await _geocode(start.value, city) or origin
    if start_loc is None:
        start_loc = CITY_CENTER.get(city, CITY_CENTER[DEFAULT_CITY])

    # --- fixed events: hard time+place anchors (§7.3, 脚本D) --------------
    fixed_sorted = sorted(
        intent.fixed_events, key=lambda e: _extract_hhmm(e.start) or "99:99"
    )
    fixed_stops: list[tuple[Stop, dict]] = []
    for ev in fixed_sorted:
        fs = _parse_clock(_extract_hhmm(ev.start), base) if _extract_hhmm(ev.start) else None
        fe = _parse_clock(_extract_hhmm(ev.end), base) if _extract_hhmm(ev.end) else None
        if fs and fe and fe <= fs:
            fe = None
        fixed_stops.append((
            Stop(kind="fixed", marker="🔒", time="", name=f"{ev.place} · {ev.title}",
                 tags=["固定日程"], location=await _geocode(ev.place, city),
                 why="你定好的硬约束，整条行程围绕它排。"),
            {"start": fs, "end": fe},
        ))
    has_fixed = bool(fixed_stops)

    # --- ground each task target (threading prev_loc for near-searches) --
    targets = _grounding_targets(intent)
    prev_loc = (fixed_stops[0][0].location or start_loc) if has_fixed else start_loc
    task_stops: list[tuple[Stop, int]] = []
    for idx, (place, task) in enumerate(targets, start=1):
        stop, dwell = await _resolve_stop(place, task, prev_loc or start_loc, city, str(idx), amap.enabled)
        task_stops.append((stop, dwell))
        if stop.location:
            prev_loc = stop.location

    # --- assemble the ordered timeline -----------------------------------
    items: list[PlanItem] = []
    if has_fixed:
        # Fixed events split the day into windows. If the user named a start
        # (e.g. hotel), include it before the first meeting and later backsolve
        # its departure time from the first hard anchor.
        if start_named:
            items.append((
                Stop(kind="start", marker="📍", time="",
                     name=start.value,
                     location=start_loc,
                     open_info="📍 " + start.value,
                     why="按你给的出发点出发。"),
                0, None, None,
            ))
        items.append((fixed_stops[0][0], 0, fixed_stops[0][1], None))
        task_items = list(zip(task_stops, targets))
        task_i = 0
        for fixed_i in range(1, len(fixed_stops)):
            prev_meta = fixed_stops[fixed_i - 1][1]
            next_stop, next_meta = fixed_stops[fixed_i]
            while task_i < len(task_items):
                (stop, dwell), (_, task) = task_items[task_i]
                if not _task_likely_between_fixed(task, prev_meta, next_meta):
                    break
                items.append((stop, dwell, None, _extract_hhmm(task.time_hint) if task else None))
                task_i += 1
            items.append((next_stop, 0, next_meta, None))
        for (stop, dwell), (_, task) in task_items[task_i:]:
            items.append((stop, dwell, None, _extract_hhmm(task.time_hint) if task else None))
        end = intent.constraints.end
        if end and end.value:
            items.append((
                Stop(kind="end", marker="🏠", time="", name=f"回 {end.value}",
                     location=await _geocode(end.value, city), why="顺路到终点，结束今天的行程。"),
                0, None, _extract_hhmm(intent.constraints.time_window.end),
            ))
    else:
        items.append((
            Stop(kind="start", marker="📍", time="",
                 name=start.value if start_named else "从当前位置出发",
                 location=start_loc,
                 open_info=("📍 " + start.value) if start_named else "📍 当前位置" + ("" if origin else "（示例）"),
                 why="按你给的出发点出发。" if start_named else "起点默认取你的当前位置，也可在对话里直接说“从 XX 出发”。"),
            0, None, None,
        ))
        for (stop, dwell), (_, task) in zip(task_stops, targets):
            items.append((stop, dwell, None, _extract_hhmm(task.time_hint) if task else None))
        end = intent.constraints.end
        if end and end.value:
            items.append((
                Stop(kind="end", marker="🏠", time="", name=f"回 {end.value}",
                     location=await _geocode(end.value, city), why="顺路到终点，结束今天的行程。"),
                0, None, _extract_hhmm(intent.constraints.time_window.end),
            ))

    return await _finalize(intent, items, base, start_clock_src, city)


async def _finalize(
    intent: IntentObject, items: list[PlanItem], base: datetime,
    start_clock_src: Optional[str], city: str,
) -> Plan:
    """Walk the assembled items into a timed Plan: legs + clock cascade + real
    polyline + nav + feasibility. Shared by initial planning and by the swap
    recompute (which assembles items from an existing timeline, no grounding)."""
    amap = get_amap()
    # --- walk: legs + clock + real polyline + feasibility ----------------
    clock = _parse_clock(start_clock_src, base)
    issues: list[str] = []
    total_distance_m = 0
    total_duration_s = 0
    max_leg_m = 0
    route_points: list[list[float]] = []
    route_segments: list[RouteSegment] = []
    stops: list[Stop] = []
    prev_stop: Optional[Stop] = None
    prev_index = 0

    async def _depart_for_next_fixed(first_stop: Stop) -> Optional[datetime]:
        next_fixed = next(
            ((s, m) for s, _, m, _ in items[1:] if m and m.get("start")),
            None,
        )
        if not next_fixed:
            return None
        next_stop, next_meta = next_fixed
        if first_stop.location and next_stop.location:
            leg, _ = await _leg_for(first_stop.location, next_stop.location)
            travel = timedelta(seconds=leg.duration_s if leg else 15 * 60)
        else:
            travel = timedelta(minutes=15)
        return next_meta["start"] - travel - timedelta(minutes=FIRST_FIXED_BUFFER_MIN)

    for i, (stop, dwell, meta, floor) in enumerate(items):
        if i == 0:
            if meta and meta["start"]:
                clock = meta["start"]
            else:
                depart = await _depart_for_next_fixed(stop)
                if depart:
                    clock = depart
            stop.time = _fixed_label(meta) if meta else clock.strftime("%H:%M")
            clock = _advance_after(clock, meta, dwell)
            stops.append(stop)
            prev_stop, prev_index = stop, 0
            continue

        # Drop a POI that resolved to (essentially) the same point as the one
        # before it — a bad patch can ground several slots to the same place;
        # don't render duplicate stops joined by 0-distance legs.
        if (stop.kind == "poi" and stop.location and prev_stop.location
                and _haversine_m(prev_stop.location, stop.location) < 50):
            continue

        if prev_stop.location and stop.location:
            leg, text = await _leg_for(prev_stop.location, stop.location)
            if leg:
                total_distance_m += leg.distance_m
                total_duration_s += leg.duration_s
                max_leg_m = max(max_leg_m, leg.distance_m)
                route_points.extend(leg.polyline)
                route_segments.append(RouteSegment(
                    from_index=prev_index, to_index=len(stops),
                    from_name=prev_stop.name, to_name=stop.name,
                    mode=leg.mode, polyline=leg.polyline,
                ))
                clock += timedelta(seconds=leg.duration_s)
            else:
                route_segments.append(RouteSegment(
                    from_index=prev_index, to_index=len(stops),
                    from_name=prev_stop.name, to_name=stop.name,
                    mode="auto", polyline=[prev_stop.location, stop.location],
                ))
                clock += timedelta(minutes=15)
        else:
            text = "出行方式按距离自动选 · 示例"
            clock += timedelta(minutes=15)

        if meta and meta["start"]:  # arriving at a hard anchor: must not be late
            arrival = clock
            if arrival > meta["start"]:
                if prev_stop and prev_stop.kind == "poi":
                    issues.append(
                        f"「{prev_stop.name}」后赶不上「{stop.name}」：约 {arrival.strftime('%H:%M')} 到，"
                        f"{meta['start'].strftime('%H:%M')} 开始；请点该站「换一个」选更近或更省时的地点"
                    )
                else:
                    issues.append(f"赶不上「{stop.name}」：约 {arrival.strftime('%H:%M')} 到，{meta['start'].strftime('%H:%M')} 开始")
                stop.leg = f"{text} · ⚠ 约 {arrival.strftime('%H:%M')} 到，已晚"
            else:
                buf = int((meta["start"] - arrival).total_seconds() // 60)
                stop.leg = f"{text} · {arrival.strftime('%H:%M')} 到，留 {buf} min 缓冲"
            stop.time = _fixed_label(meta)
            clock = _advance_after(meta["start"], meta, dwell)
        else:
            if floor:  # respect ">=11:00" style hints: wait, don't arrive early
                floor_dt = _parse_clock(floor, clock)
                if clock < floor_dt:
                    if stop.kind == "end":
                        text += f" · 可按 {floor} 抵达"
                    else:
                        text += f" · 等到 {floor} 再开始"
                    clock = floor_dt
            stop.time = clock.strftime("%H:%M")
            suffix = f" · 停留约 {dwell} min" if stop.kind == "poi" and dwell else ""
            stop.leg = text + suffix
            clock += timedelta(minutes=dwell)

        stops.append(stop)
        prev_stop, prev_index = stop, len(stops) - 1

    # day-level feasibility checks
    if clock.date() != base.date():
        issues.append("行程跨越了午夜，请确认时间安排")
    tw_end = _extract_hhmm(intent.constraints.time_window.end)
    if tw_end:
        limit = _parse_clock(tw_end, base)
        if clock > limit and clock.date() == base.date():
            issues.append(f"预计 {clock.strftime('%H:%M')} 结束，比你希望的 {tw_end} 晚")
    # cross-city / absurd distance: a single >120km leg means the stops aren't in
    # one city. We only do intra-city planning, so flag it instead of drawing a
    # 1200km "drive" from 北京 to 上海 and calling it feasible.
    if max_leg_m > 120_000:
        issues.append(f"有一段约 {round(max_leg_m / 1000)} km，疑似跨城市；当前按市内规划，跨城请分开安排")

    # --- summary / nav / feasibility -------------------------------------
    located = [s for s in stops if s.location]
    nav = NavLinks()
    if len(located) >= 2:
        nav = build_amap_nav_uri(
            (located[0].location[0], located[0].location[1], located[0].name),
            (located[-1].location[0], located[-1].location[1], located[-1].name),
            [(s.location[0], s.location[1], s.name) for s in located[1:-1]],
        )
    n_poi = sum(1 for s in stops if s.kind == "poi")
    summary = RouteSummary(
        total_distance_text=(f"~{round(total_distance_m / 1000)} km" if total_distance_m else "示例"),
        total_duration_text=(f"{round(total_duration_s / 60)} m" if total_duration_s else "示例"),
        stop_count=n_poi,
        polyline=route_points,  # real road geometry for the map
        segments=route_segments,
    )
    placeholders = [s for s in stops if s.kind == "poi" and not s.location]
    real_pois = [s for s in stops if s.kind == "poi" and s.location]
    miss = "、".join(s.name.replace("（示例）", "") for s in placeholders[:3])
    if not amap.enabled:
        feasibility = Feasibility(ok=True, note="示例数据 · 配置高德 Key 后为真实检索与耗时")
    elif placeholders and not real_pois:
        # Don't claim "已按真实地点" when nothing was actually grounded.
        feasibility = Feasibility(ok=False, note=f"⚠ 没找到匹配的真实地点（{miss}），换个更具体的名字我再帮你排")
    elif issues:
        feasibility = Feasibility(ok=False, note="⚠ " + "；".join(issues))
    elif placeholders:
        feasibility = Feasibility(ok=True, note=f"其余已按真实地点排好；只有「{miss}」没找到真实匹配，可换个说法")
    else:
        # Note: we don't actually verify opening hours yet, so don't claim to.
        feasibility = Feasibility(ok=True, note="已按真实地点与路程排好 · 行程可行 ✓")
    return Plan(
        city=city,
        panel_hint=f"{n_poi} 站",
        understanding=build_understanding(intent),
        summary=summary,
        timeline=stops,
        nav=nav,
        feasibility=feasibility,
        intent=intent,
        source="live" if amap.enabled else "mock",
    )


async def recompute_plan(timeline: list[Stop], city: str, intent: Optional[IntentObject]) -> Plan:
    """Re-route an existing timeline after the user swapped a stop to one of its
    alternatives — deterministic, no re-grounding, no LLM. The client has already
    replaced the swapped stop's name/location; we just recompute legs/times/nav."""
    iv = intent or IntentObject()
    base = datetime.now() + timedelta(days=_date_offset(iv.date))
    start_src = (_earliest_anchor(iv) or (_extract_hhmm(timeline[0].time) if timeline else None))
    items: list[PlanItem] = []
    for s in timeline:
        meta = None
        if s.kind == "fixed":
            parts = (s.time or "").split("–")
            fs = _parse_clock(_extract_hhmm(parts[0]), base) if _extract_hhmm(parts[0]) else None
            fe = _parse_clock(_extract_hhmm(parts[1]), base) if len(parts) > 1 and _extract_hhmm(parts[1]) else None
            meta = {"start": fs, "end": fe}
        items.append((s, s.dwell_min, meta, None))
    return await _finalize(iv, items, base, start_src, city)
