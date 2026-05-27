"""Controlled vocabulary + tag->keyword mapping (PROPOSAL §6.4.2).

The model only sticks *tags* onto a request; the actual AMap search keywords
come from this table. That decoupling kills synonym drift ("安静/宁静/清净")
and makes runs reproducible and tunable without touching the model.
"""

from __future__ import annotations

# Half-closed vibe vocabulary. `other` is the escape hatch.
VIBE_TAGS: tuple[str, ...] = (
    "安静", "慢节奏", "治愈", "自然", "水边", "文艺", "复古",
    "热闹", "烟火气", "夜景", "观景", "喝茶", "小吃", "成都味", "city walk",
    "other",
)

AVOID_TAGS: tuple[str, ...] = (
    "嘈杂", "排队", "赶时间", "人挤人", "太累", "大众化", "油腻",
)

# tag -> AMap search keywords (used by P3 / the deterministic search step).
TAG_TO_KEYWORDS: dict[str, list[str]] = {
    "安静": ["咖啡馆", "书店", "公园"],
    "慢节奏": ["咖啡馆", "茶馆"],
    "治愈": ["书店", "美术馆", "花店", "咖啡馆"],
    "自然": ["公园", "湖", "植物园"],
    "水边": ["河", "湖", "滨水公园"],
    "文艺": ["美术馆", "书店", "文创园"],
    "复古": ["老街", "胡同", "文创园"],
    "热闹": ["商圈", "步行街", "夜市"],
    "烟火气": ["夜市", "小吃街", "市集"],
    "夜景": ["观景台", "江边", "天台酒吧"],
    "观景": ["观景台", "公园", "高处"],
    "喝茶": ["茶馆", "茶社"],
    "小吃": ["小吃", "美食街"],
    "成都味": ["茶馆", "火锅", "川菜"],
    "city walk": ["步行街", "公园", "老街"],
}

# desired_category -> keywords (coarser buckets the model may emit).
CATEGORY_TO_KEYWORDS: dict[str, list[str]] = {
    "brunch/西餐": ["brunch", "西餐厅", "咖啡馆"],
    "公园/书店/咖啡": ["公园", "书店", "咖啡馆"],
    "城市观景/高处": ["观景台", "公园"],
    "餐饮": ["餐厅"],
    "咖啡": ["咖啡馆"],
}

# Default dwell minutes per task type (§7.1 task-type library); user-editable.
TASK_DWELL_MIN: dict[str, int] = {
    "dining": 60,
    "leisure": 90,
    "sightseeing": 70,
    "shopping": 90,
    "sports": 90,
    "pickup": 10,
    "meeting": 120,
    "commute": 0,
    "other": 60,
}

# Default AMap POI keyword per task type, for tasks that need a POI.
TASK_DEFAULT_KEYWORD: dict[str, str] = {
    "dining": "餐厅",
    "leisure": "咖啡馆",
    "sightseeing": "景点",
    "shopping": "商场",
    "sports": "运动场馆",
    "meeting": "会议",
    "other": "地点",
}


def keywords_for_tags(tags: list[str], categories: list[str] | None = None) -> list[str]:
    """Map a set of vibe tags + desired categories to a de-duplicated,
    order-preserving list of AMap search keywords."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        for kw in TAG_TO_KEYWORDS.get(tag, []):
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
    for cat in categories or []:
        for kw in CATEGORY_TO_KEYWORDS.get(cat, []):
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
    return out
