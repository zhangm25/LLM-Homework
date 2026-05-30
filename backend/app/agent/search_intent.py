"""Normalize vague user needs into constrained map-search intent.

The LLM may help translate natural language ("午饭", "安静坐坐") into POI
search terms, but the backend keeps control over the allowed AMap type codes
and execution strategy.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..debug_log import write_debug_log
from ..llm.client import get_llm
from ..models.intent import Task


SearchCategory = Literal[
    "dining",
    "coffee_tea",
    "quiet_rest",
    "shopping",
    "sightseeing",
    "sports",
    "generic",
]

ALLOWED_TYPE_CODES: dict[str, str] = {
    "dining": "050000",
    "coffee_tea": "050000",
    "shopping": "060000",
    "sightseeing": "110000",
    "sports": "080000",
}

GENERIC_DINING_TERMS = {
    "",
    "饭",
    "饭菜",
    "东西",
    "点东西",
    "吃饭",
    "吃点东西",
    "午饭",
    "午餐",
    "中饭",
    "晚饭",
    "晚餐",
    "餐厅",
    "用餐",
}


class MapSearchIntent(BaseModel):
    raw_need: str = ""
    search_category: SearchCategory = "generic"
    keywords: list[str] = Field(default_factory=list)
    type_codes: list[str] = Field(default_factory=list)
    radius_m: int = 3000
    fallback_radius_m: int = 8000
    reason: str = ""
    source: Literal["llm", "fallback", "llm_with_fallback"] = "fallback"

    @field_validator("keywords", mode="before")
    @classmethod
    def _clean_keywords(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out[:6]

    @field_validator("type_codes", mode="before")
    @classmethod
    def _clean_type_codes(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = value.split("|")
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            code = str(item or "").strip()
            if code in ALLOWED_TYPE_CODES.values() and code not in out:
                out.append(code)
        return out[:4]

    @field_validator("radius_m", "fallback_radius_m", mode="before")
    @classmethod
    def _clamp_radius(cls, value: Any) -> int:
        try:
            return max(500, min(int(value), 50_000))
        except (TypeError, ValueError):
            return 3000

    @property
    def keyword_param(self) -> str:
        text = "|".join(self.keywords)
        return text[:80]

    @property
    def type_param(self) -> Optional[str]:
        return "|".join(self.type_codes) if self.type_codes else None


def _strip_need(text: str) -> str:
    s = (text or "").strip()
    for prefix in ("吃个", "吃点", "喝个", "喝点", "找个", "看看", "去", "到", "吃", "喝", "找", "逛"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip(" 的个家")


def _local_search_intent(task: Optional[Task], raw_need: str, category_hint: Optional[str]) -> MapSearchIntent:
    text = _strip_need(raw_need or category_hint or "")
    task_type = task.type if task else "other"
    haystack = f"{raw_need or ''} {category_hint or ''} {text}"

    if task_type == "dining" or any(token in haystack for token in ("吃", "饭", "餐", "美食", "小吃", "brunch")):
        if text in GENERIC_DINING_TERMS or not text:
            keywords = ["餐厅", "饭店", "中餐", "快餐"]
        else:
            keywords = [text, "餐厅"]
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="dining",
            keywords=keywords,
            type_codes=["050000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local dining normalization",
        )

    if any(token in haystack for token in ("咖啡", "茶", "奶茶", "坐坐", "休息", "安静")):
        if text in {"咖啡馆", "咖啡厅", "茶馆", "茶社", "书店", "公园"}:
            keywords = [text]
        else:
            keywords = ["咖啡馆", "茶馆", "书店"] if "安静" in haystack or "坐坐" in haystack else ["咖啡馆", "茶馆"]
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="quiet_rest" if "安静" in haystack or "坐坐" in haystack else "coffee_tea",
            keywords=keywords,
            type_codes=["050000"] if len(keywords) <= 2 else [],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local rest/cafe normalization",
        )

    if task_type == "shopping" or any(token in haystack for token in ("购物", "商场", "逛")):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="shopping",
            keywords=[text or "商场", "购物中心"] if text and text != "商场" else ["商场", "购物中心"],
            type_codes=["060000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local shopping normalization",
        )

    if task_type == "sports":
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="sports",
            keywords=[text or "运动场馆"],
            type_codes=["080000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local sports normalization",
        )

    return MapSearchIntent(
        raw_need=raw_need,
        search_category="generic",
        keywords=[text or raw_need or category_hint or "地点"],
        radius_m=3000,
        fallback_radius_m=8000,
        reason="local generic normalization",
    )


def _validate_llm_intent(data: dict, fallback: MapSearchIntent) -> MapSearchIntent:
    try:
        intent = MapSearchIntent(**data, source="llm")
    except ValidationError:
        fallback.source = "llm_with_fallback"
        return fallback
    if not intent.keywords:
        intent.keywords = fallback.keywords
        intent.source = "llm_with_fallback"
    allowed = ALLOWED_TYPE_CODES.get(intent.search_category)
    if allowed and allowed not in intent.type_codes:
        intent.type_codes = [allowed]
        intent.source = "llm_with_fallback"
    if not intent.type_codes and fallback.type_codes and intent.search_category == fallback.search_category:
        intent.type_codes = fallback.type_codes
    intent.radius_m = max(500, min(intent.radius_m or fallback.radius_m, 50_000))
    intent.fallback_radius_m = max(intent.radius_m, min(intent.fallback_radius_m or fallback.fallback_radius_m, 50_000))
    return intent


def _log_search_intent(intent: MapSearchIntent, llm_raw: Optional[dict] = None) -> None:
    write_debug_log(
        "MAP SEARCH INTENT\n"
        f"intent: {json.dumps(intent.model_dump(), ensure_ascii=False)}\n"
        f"amap_keywords: {intent.keyword_param}\n"
        f"amap_types: {intent.type_param}\n"
        f"llm_raw: {json.dumps(llm_raw, ensure_ascii=False, default=str) if llm_raw is not None else None}"
    )


SEARCH_INTENT_SYSTEM = """你是 RoamMind 的地图搜索意图归一化模块。你的任务不是规划行程，也不是选择具体店铺，而是把用户的模糊活动需求转换成稳定的高德 POI 搜索意图 JSON。

高德 POI 搜索规则摘要：
1. 周边搜索 endpoint 由后端决定，你不要输出 endpoint 或 key。
2. keywords 是地点/业态关键字，不要把“午饭/晚饭/吃点东西/坐坐”这类生活语义原样当成唯一关键词。
3. keywords 可用多个词，用数组输出；后端会用“|”拼接，拼接后总长度不能超过 80 字符。
4. types 只能从白名单里选：050000=餐饮服务，060000=购物服务，080000=体育休闲服务，110000=风景名胜。不要输出其他 code。
5. 对“午饭/晚饭/吃饭/吃点东西”，优先 search_category=dining，keywords 可给“餐厅、饭店、中餐、快餐”，type_codes 给 ["050000"]。
6. 对“咖啡/茶/安静坐坐/休息”，可用 coffee_tea 或 quiet_rest；如果同时包含书店/公园这类混合业态，type_codes 可为空，keywords 给多个候选业态。
7. radius_m 通常 3000；路线上的餐饮/咖啡不要扩到全城。fallback_radius_m 可为 8000。
8. 只输出 JSON，不要解释。

输出格式：
{
  "raw_need": str,
  "search_category": "dining"|"coffee_tea"|"quiet_rest"|"shopping"|"sightseeing"|"sports"|"generic",
  "keywords": [str],
  "type_codes": [str],
  "radius_m": int,
  "fallback_radius_m": int,
  "reason": str
}"""


SEARCH_INTENT_USER = """最小相关上下文：
- 城市：{city}
- 命名区域/地点：{place}
- 任务类型：{task_type}
- 用户任务原文：{raw_need}
- 后端已有类别提示：{category_hint}
- 搜索模式：{mode}
- 锚点坐标：{anchors}

请输出地图搜索意图 JSON。"""


async def build_map_search_intent(
    task: Optional[Task],
    raw_need: str,
    category_hint: Optional[str],
    city: str,
    mode: str,
    anchors: list[list[float]] | None = None,
    place: Optional[str] = None,
) -> MapSearchIntent:
    fallback = _local_search_intent(task, raw_need, category_hint)
    llm = get_llm()
    if not llm.enabled:
        _log_search_intent(fallback)
        return fallback

    user = SEARCH_INTENT_USER.format(
        city=city,
        place=place or "",
        task_type=task.type if task else "",
        raw_need=raw_need,
        category_hint=category_hint or "",
        mode=mode,
        anchors=anchors or [],
    )
    try:
        data = await llm.complete_json(SEARCH_INTENT_SYSTEM, user, stage="map_search_intent")
    except Exception as exc:
        fallback.source = "llm_with_fallback"
        fallback.reason = f"{fallback.reason}; llm failed: {type(exc).__name__}"
        _log_search_intent(fallback)
        return fallback
    intent = _validate_llm_intent(data, fallback)
    _log_search_intent(intent, data)
    return intent
