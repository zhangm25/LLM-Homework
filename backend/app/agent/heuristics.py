"""Rule-based Intent Object extraction.

Used when no DeepSeek key is set (or P1 fails), so free-text input still
produces a structurally-valid Intent Object. Deliberately simple keyword
matching — the LLM path (P1) is the real extractor; this is the safety net.
"""

from __future__ import annotations

import re

from ..models.intent import (
    Constraints,
    Endpoint,
    ExplicitPOI,
    ImplicitPreferences,
    IntentObject,
    Task,
)
from ..llm.vocab import AVOID_TAGS, VIBE_TAGS, TASK_DWELL_MIN
from .place_norm import normalize_place_name

_CITIES = ("北京", "上海", "成都", "广州", "深圳", "杭州", "重庆", "西安", "南京", "武汉")

# keyword -> (task type, intent label)
_TASK_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("接一下", "接上", "接我", "接人", "接女朋友", "接男朋友"), "pickup", "接人"),
    (("看电影", "电影", "影院"), "leisure", "看电影"),
    (("brunch", "早午餐"), "dining", "brunch"),
    (("烤鸭",), "dining", "烤鸭"),
    (("火锅",), "dining", "火锅"),
    (("午饭", "中午", "午餐"), "dining", "午饭"),
    (("晚饭", "晚餐"), "dining", "晚饭"),
    (("吃", "餐", "美食", "小吃"), "dining", "吃饭"),
    (("喝茶", "茶馆", "盖碗"), "leisure", "喝茶"),
    (("咖啡", "书店", "安静", "待着", "歇", "发呆", "放松"), "leisure", "安静待着"),
    (("日落", "黄昏", "夜景", "观景", "看看城市", "天际线"), "sightseeing", "看城市/夜景"),
    (("逛", "散步", "公园", "city walk"), "sightseeing", "逛逛"),
    (("购物", "逛街", "商场"), "shopping", "购物"),
    (("开会", "会议", "见客户"), "meeting", "开会"),
]

_MOOD_RULES: list[tuple[tuple[str, ...], str]] = [
    (("累", "疲惫", "乏"), "有点累"),
    (("静", "安静", "清净"), "想静一静"),
    (("悠闲", "慢慢", "放松", "随意"), "想悠闲"),
    (("开心", "热闹", "嗨"), "想热闹一下"),
]


def extract_intent_heuristic(message: str, city_hint: str | None) -> IntentObject:
    text = message or ""
    lower = text.lower()

    city = next((c for c in _CITIES if c in text), None) or city_hint

    end_value = _extract_end_value(text)

    tasks: list[Task] = []
    used_types: set[str] = set()
    for keywords, ttype, label in _TASK_RULES:
        if any(k.lower() in lower for k in keywords) and label not in {t.intent for t in tasks}:
            tasks.append(Task(
                id=f"t{len(tasks) + 1}", type=ttype, intent=label,
                dwell_min=TASK_DWELL_MIN.get(ttype, 60), needs_poi=True,
            ))
            used_types.add(ttype)
    vibe = [t for t in VIBE_TAGS if t != "other" and t in text]
    avoid = [t for t in AVOID_TAGS if t in text]
    mood = next((label for kws, label in _MOOD_RULES if any(k in text for k in kws)), None)

    # Only conjure a generic "wander" task when there is *some* signal (a mood,
    # a vibe, or a destination). Pure noise ("1+1等于几") yields no task, so the
    # pipeline asks a clarifying question instead of inventing a trip. Named
    # places are recovered later by augment_intent, which also makes plannable.
    if not tasks and (mood or vibe or end_value):
        tasks.append(Task(id="t1", type="leisure", intent="随便逛逛", dwell_min=90, needs_poi=True))

    return IntentObject(
        implicit_preferences=ImplicitPreferences(mood=mood, vibe_tags=vibe, avoid_tags=avoid),
        constraints=Constraints(
            city=city,
            start=Endpoint(type="current", source="default"),
            end=Endpoint(type="named", value=end_value) if end_value else None,
        ),
        tasks=tasks,
    )


# --- deterministic safety net (applied on top of LLM or heuristic output) ---
_START_AT_RE = re.compile(
    r"(?:我现在在|现在在|我在|目前在|人在)([一-龥A-Za-z0-9·]{2,16}?)(?:出发|附近|这边|这儿|，|,|。|；|;| |想|要|准备|打算|$)"
)
_START_FROM_RE = re.compile(r"从([一-龥A-Za-z0-9·]{2,16}?)出发")
_VAGUE_PLACES = {"这里", "这儿", "那里", "附近", "家里"}
# "去X" / "到X" place mentions (X stops before an activity verb or punctuation).
_GOTO_RE = re.compile(r"(?:去|到)([一-龥A-Za-z0-9·]{2,18}?)(?:逛|玩|看|吃|喝|散步|附近|接|带|睡觉|，|,|。|；|;| |的|$)")
_VISIT_AT_RE = re.compile(r"(?:去一下|去下|到)([一-龥A-Za-z0-9·]{2,18}?)(?:，|,|。|；|;| |再|然后|$)")
_END_RE = re.compile(r"(?:最后)?(?:回到|回)([一-龥A-Za-z0-9·]{2,18}?)(?:睡觉|休息|的家|家|，|,|。|；|;| |$)")
_PICKUP_AT_RE = re.compile(
    r"(?:中途)?(?:去|到)([一-龥A-Za-z0-9·]{2,18}?)(?:接一下|接下|接上|接我|接)(?:我)?(?:的)?(?:女朋友|男朋友|朋友|同学|家人|人)?"
)
_NOT_PLACE = {
    "哪", "哪里", "哪儿", "那里", "这里", "这儿", "附近", "外面", "街上",
    "逛逛", "走走", "玩玩", "散步", "吃饭", "饭", "电影", "看电影",
}
_PLACE_TASK_HINTS: tuple[tuple[tuple[str, ...], str, str, int], ...] = (
    (("去一下", "去下"), "commute", "去一下", 10),
    (("吃", "餐", "饭"), "dining", "吃饭", 60),
    (("找朋友",), "leisure", "找朋友", 30),
    (("找人",), "leisure", "找人", 30),
    (("接",), "pickup", "接人", 10),
    (("电影",), "leisure", "看电影", 120),
    (("逛",), "sightseeing", "逛逛", 70),
    (("散步",), "sightseeing", "散步", 70),
    (("购物",), "shopping", "购物", 90),
)
_PLACE_SPLIT_MARKERS = (
    "接一下", "接下", "接上", "接我", "接女朋友", "接男朋友", "接朋友",
    "带上", "带我", "和我", "跟我", "一起", "然后", "再去", "顺便",
)


def _clean_place_name(name: str) -> str:
    return normalize_place_name(name) or ""


def augment_intent(intent: IntentObject, message: str) -> IntentObject:
    """Backfill a named start / city / explicit places straight from the
    utterance when the model missed them — so these critical anchors never
    depend on LLM consistency (e.g. v4-flash dropping "798")."""
    text = message or ""

    if not (intent.constraints.start.type == "named" and intent.constraints.start.value):
        m = _START_AT_RE.search(text) or _START_FROM_RE.search(text)
        if m:
            value = m.group(1).strip()
            if value and value not in _VAGUE_PLACES:
                intent.constraints.start = Endpoint(type="named", value=value, source="nl_extract")

    if not intent.constraints.city:
        city = next((c for c in _CITIES if c in text), None)
        if city:
            intent.constraints.city = city

    if not (intent.constraints.end and intent.constraints.end.value):
        end_value = _extract_end_value(text)
        if end_value:
            intent.constraints.end = Endpoint(type="named", value=end_value, source="nl_extract")

    recovered = _recover_ordered_places(text, intent.constraints.start.value)
    if recovered and (not intent.explicit_pois or _places_look_noisy(intent.explicit_pois)):
        intent.explicit_pois = [ExplicitPOI(name=n, fixed_order_index=i) for i, n in enumerate(recovered)]

    # Clean trailing action words off every place name (LLM- or regex-sourced).
    if intent.constraints.start.type == "named" and intent.constraints.start.value:
        intent.constraints.start.value = _clean_place_name(intent.constraints.start.value)
    cleaned: list[ExplicitPOI] = []
    for poi in intent.explicit_pois:
        name = _clean_place_name(poi.name)
        if intent.constraints.end and intent.constraints.end.value and name == intent.constraints.end.value:
            continue
        if any(existing.name == name for existing in cleaned):
            continue
        if name:
            poi.name = name
            cleaned.append(poi)
    intent.explicit_pois = cleaned
    _align_tasks_to_recovered_places(intent, text)

    return intent


def _extract_end_value(text: str) -> str | None:
    m = _END_RE.search(text or "")
    if not m:
        return None
    value = normalize_place_name(m.group(1).strip(" 的个家"))
    return value or None


def _recover_ordered_places(text: str, start_val: str | None) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    matches: list[tuple[int, str]] = []
    for m in _PICKUP_AT_RE.finditer(text):
        matches.append((m.start(), m.group(1)))
    for m in _VISIT_AT_RE.finditer(text):
        matches.append((m.start(), m.group(1)))
    for m in _GOTO_RE.finditer(text):
        matches.append((m.start(), m.group(1)))
    for _, raw in sorted(matches, key=lambda x: x[0]):
        name = _clean_place_name(raw.strip(" 的个家"))
        if name and name not in _NOT_PLACE and name != start_val and name not in seen:
            seen.add(name)
            found.append(name)
    return found


def _places_look_noisy(places: list[ExplicitPOI]) -> bool:
    markers = _PLACE_SPLIT_MARKERS + ("接", "带", "一起", "最后", "帮我")
    return any(any(marker in p.name for marker in markers) for p in places)


def _align_tasks_to_recovered_places(intent: IntentObject, text: str) -> None:
    if not intent.explicit_pois:
        return
    wanted = _recover_ordered_tasks(text)
    if wanted and (_tasks_look_generic_or_misaligned(intent.tasks) or len(intent.tasks) < min(len(wanted), len(intent.explicit_pois))):
        place_names = [p.name for p in intent.explicit_pois]
        _attach_tasks_to_places(wanted, place_names, text)
        intent.tasks = [
            Task(
                id=f"t{i + 1}",
                type=t.type,
                intent=t.intent,
                dwell_min=t.dwell_min,
                needs_poi=t.needs_poi,
                at=t.at,
                explicit=t.explicit,
            )
            for i, t in enumerate(wanted)
        ]


def _attach_tasks_to_places(tasks: list[Task], places: list[str], text: str) -> None:
    used: set[str] = set()
    for task in tasks:
        best_place: str | None = None
        best_pos: int | None = None
        for place in places:
            if place in used:
                continue
            pos = text.find(place)
            if pos < 0:
                continue
            window = text[pos : pos + len(place) + 12]
            if task.intent and (task.intent in window or any(k in window for k in _task_keywords(task))):
                if best_pos is None or pos < best_pos:
                    best_pos = pos
                    best_place = place
        if best_place:
            task.at = best_place
            used.add(best_place)


def _task_keywords(task: Task) -> tuple[str, ...]:
    if task.type == "dining":
        return ("吃", "餐", "饭")
    if task.type == "leisure" and "找" in task.intent:
        return ("找朋友", "找人", "找")
    if task.type == "pickup":
        return ("接", "带上")
    if "电影" in task.intent:
        return ("电影", "影院")
    if task.type == "sightseeing":
        return ("逛", "散步", "日落", "观景")
    return (task.intent,)


def _recover_ordered_tasks(text: str) -> list[Task]:
    specs: list[tuple[tuple[str, ...], str, str, int]] = list(_PLACE_TASK_HINTS) + [
        (("看电影", "影院"), "leisure", "看电影", 120),
        (("逛逛", "city walk"), "sightseeing", "逛逛", 70),
        (("走走",), "sightseeing", "散步", 70),
        (("日落", "黄昏", "观景", "夜景"), "sightseeing", "看日落/观景", 70),
        (("午饭", "晚饭", "吃点"), "dining", "吃饭", 60),
    ]
    hits: list[tuple[int, str, str, int]] = []
    seen_labels: set[str] = set()
    for keywords, ttype, label, dwell in specs:
        positions = [text.find(k) for k in keywords if k in text]
        if not positions or label in seen_labels:
            continue
        seen_labels.add(label)
        hits.append((min(positions), ttype, label, dwell))
    return [
        Task(
            id=f"t{i + 1}",
            type=ttype,
            intent=label,
            dwell_min=dwell,
            needs_poi=True,
            explicit=True,
        )
        for i, (_, ttype, label, dwell) in enumerate(sorted(hits, key=lambda x: x[0]))
    ]


def _tasks_look_generic_or_misaligned(tasks: list[Task]) -> bool:
    if not tasks:
        return True
    generic = {"吃饭", "逛逛", "随便逛逛", "安静待着"}
    return all(t.intent in generic or not t.explicit for t in tasks)
