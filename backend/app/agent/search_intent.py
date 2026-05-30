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
SearchScope = Literal[
    "exact_place",
    "around_anchor",
    "around_route",
    "in_area",
    "citywide_ranked",
    "region_ranked",
]
AnchorPolicy = Literal["none", "prev", "next", "prev_next", "area"]
RankingPolicy = Literal[
    "relevance",
    "distance_then_rating",
    "route_detour_then_distance",
    "rating_then_relevance",
    "vibe_then_rating",
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
AROUND_CUES = ("附近", "周边", "旁边", "就近", "顺路", "路上", "途经", "沿途", "随便找", "找家", "找一家")
RANKED_CUES = ("最好", "好吃", "评分高", "高分", "热门", "推荐", "值得", "景色好", "风景好", "放松")
CHAIN_OR_CATEGORY_TERMS = (
    "麦当劳", "肯德基", "kfc", "KFC", "星巴克", "瑞幸", "manner", "Manner",
    "超市", "便利店", "药店", "书店", "餐厅", "饭店", "火锅", "小吃", "美食", "咖啡馆", "咖啡厅", "茶馆",
)
FOOD_CHAIN_TERMS = ("麦当劳", "肯德基", "kfc", "KFC")
COFFEE_CHAIN_TERMS = ("星巴克", "瑞幸", "manner", "Manner")
SHOPPING_CATEGORY_TERMS = ("超市", "便利店", "药店")
SCENIC_RELAX_TERMS = ("景色", "风景", "放松", "自然", "公园", "湖", "观景")


class MapSearchIntent(BaseModel):
    raw_need: str = ""
    search_category: SearchCategory = "generic"
    search_scope: SearchScope = "around_anchor"
    anchor_policy: AnchorPolicy = "prev"
    ranking_policy: RankingPolicy = "distance_then_rating"
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
    for prefix in ("顺路去个", "顺路去", "附近的", "附近", "找一家", "找家", "找一个", "吃个", "吃点", "喝个", "喝点", "找个", "看看", "去", "到", "吃", "喝", "找", "逛"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip(" 的个家一")


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _first_token(text: str, tokens: tuple[str, ...]) -> Optional[str]:
    lowered = text.lower()
    for token in tokens:
        if token.lower() in lowered:
            return token.upper() if token.lower() == "kfc" else token
    return None


def _scope_for_need(haystack: str, mode: str) -> tuple[SearchScope, AnchorPolicy, RankingPolicy]:
    if mode == "area_around":
        return "in_area", "area", "distance_then_rating"
    if _has_any(haystack, AROUND_CUES):
        return "around_route", "prev_next", "route_detour_then_distance"
    if _has_any(haystack, RANKED_CUES):
        return "citywide_ranked", "none", "rating_then_relevance"
    if _has_any(haystack, CHAIN_OR_CATEGORY_TERMS):
        return "around_route", "prev_next", "distance_then_rating"
    if mode == "route_anchor_around":
        return "around_route", "prev_next", "route_detour_then_distance"
    return "around_anchor", "prev", "distance_then_rating"


def _scope_is_locally_locked(fallback: MapSearchIntent) -> bool:
    """Some scopes are deterministic safety rules, not LLM preferences.

    Brand chains, category shops and explicit nearby/route wording must stay
    around-anchored even if the model suggests a broad region search.
    """
    text = fallback.raw_need or ""
    return (
        fallback.search_scope in {"around_anchor", "around_route", "in_area"}
        and (
            _has_any(text, AROUND_CUES)
            or _has_any(text, CHAIN_OR_CATEGORY_TERMS)
            or "chain normalization" in fallback.reason
            or "nearby shopping/service" in fallback.reason
        )
    )


def looks_like_chain_or_category_place(text: Optional[str]) -> bool:
    return _has_any(text or "", CHAIN_OR_CATEGORY_TERMS)


def _looks_exact_place(text: str, task: Optional[Task], mode: str) -> bool:
    if mode != "named_or_fuzzy_place":
        return False
    haystack = text or ""
    if task is not None:
        return False
    if _has_any(haystack, AROUND_CUES + RANKED_CUES + CHAIN_OR_CATEGORY_TERMS):
        return False
    if any(token in haystack for token in ("门", "校区", "公园", "大学", "酒店", "大厦", "购物中心", "地铁站")):
        return True
    return len(haystack) >= 2


def _dining_keywords(text: str) -> list[str]:
    if text in GENERIC_DINING_TERMS or not text:
        return ["餐厅", "饭店", "中餐", "快餐"]
    if "火锅" in text:
        return ["火锅", "火锅店", "餐厅"]
    return [text, "餐厅"]


def _local_search_intent(task: Optional[Task], raw_need: str, category_hint: Optional[str], mode: str) -> MapSearchIntent:
    text = _strip_need(raw_need or category_hint or "")
    task_type = task.type if task else "other"
    haystack = f"{raw_need or ''} {category_hint or ''} {text}"
    scope, anchor_policy, ranking_policy = _scope_for_need(haystack, mode)

    if _looks_exact_place(text, task, mode):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="generic",
            search_scope="exact_place",
            anchor_policy="none",
            ranking_policy="relevance",
            keywords=[text],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local exact place classification",
        )

    if _has_any(haystack, FOOD_CHAIN_TERMS):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="dining",
            search_scope=scope,
            anchor_policy=anchor_policy,
            ranking_policy=ranking_policy,
            keywords=[_first_token(haystack, FOOD_CHAIN_TERMS) or text or raw_need],
            type_codes=["050000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local food chain normalization",
        )

    if _has_any(haystack, COFFEE_CHAIN_TERMS):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="coffee_tea",
            search_scope=scope,
            anchor_policy=anchor_policy,
            ranking_policy=ranking_policy,
            keywords=[_first_token(haystack, COFFEE_CHAIN_TERMS) or text or raw_need],
            type_codes=["050000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local coffee chain normalization",
        )

    if _has_any(haystack, SHOPPING_CATEGORY_TERMS):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="shopping",
            search_scope=scope,
            anchor_policy=anchor_policy,
            ranking_policy=ranking_policy,
            keywords=[text or raw_need],
            type_codes=["060000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local nearby shopping/service normalization",
        )

    if task_type == "dining" or any(token in haystack for token in ("吃", "饭", "餐", "美食", "小吃", "brunch")):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="dining",
            search_scope=scope,
            anchor_policy=anchor_policy,
            ranking_policy=ranking_policy,
            keywords=_dining_keywords(text),
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
            search_scope="region_ranked" if _has_any(haystack, RANKED_CUES) else scope,
            anchor_policy="none" if _has_any(haystack, RANKED_CUES) and not _has_any(haystack, AROUND_CUES) else anchor_policy,
            ranking_policy="vibe_then_rating" if _has_any(haystack, RANKED_CUES) else ranking_policy,
            keywords=keywords,
            type_codes=["050000"] if len(keywords) <= 2 else [],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local rest/cafe normalization",
        )

    if _has_any(haystack, SCENIC_RELAX_TERMS):
        ranked = _has_any(haystack, RANKED_CUES) or "放松" in haystack
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="sightseeing" if not any(token in haystack for token in ("坐坐", "休息")) else "quiet_rest",
            search_scope="region_ranked" if ranked and not _has_any(haystack, AROUND_CUES) else scope,
            anchor_policy="none" if ranked and not _has_any(haystack, AROUND_CUES) else anchor_policy,
            ranking_policy="vibe_then_rating" if ranked else ranking_policy,
            keywords=["公园", "湖", "观景台", "景区"],
            type_codes=["110000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local scenic relax normalization",
        )

    if task_type == "shopping" or any(token in haystack for token in ("购物", "商场", "逛")):
        return MapSearchIntent(
            raw_need=raw_need,
            search_category="shopping",
            search_scope=scope,
            anchor_policy=anchor_policy,
            ranking_policy=ranking_policy,
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
            search_scope=scope,
            anchor_policy=anchor_policy,
            ranking_policy=ranking_policy,
            keywords=[text or "运动场馆"],
            type_codes=["080000"],
            radius_m=3000,
            fallback_radius_m=8000,
            reason="local sports normalization",
        )

    return MapSearchIntent(
        raw_need=raw_need,
        search_category="generic",
        search_scope=scope,
        anchor_policy=anchor_policy,
        ranking_policy=ranking_policy,
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
    if fallback.search_scope != "exact_place" and intent.search_scope == "exact_place":
        intent.search_scope = fallback.search_scope
        intent.anchor_policy = fallback.anchor_policy
        intent.ranking_policy = fallback.ranking_policy
        intent.source = "llm_with_fallback"
    if _scope_is_locally_locked(fallback) and intent.search_scope != fallback.search_scope:
        intent.search_category = fallback.search_category
        intent.search_scope = fallback.search_scope
        intent.anchor_policy = fallback.anchor_policy
        intent.ranking_policy = fallback.ranking_policy
        intent.source = "llm_with_fallback"
        intent.reason = f"{intent.reason}; backend locked scope: {fallback.reason}".strip("; ")
    if _scope_is_locally_locked(fallback) and (
        "chain normalization" in fallback.reason or "nearby shopping/service" in fallback.reason
    ):
        intent.search_category = fallback.search_category
        intent.keywords = fallback.keywords
        intent.source = "llm_with_fallback"
    if fallback.type_codes and (
        intent.search_category == fallback.search_category
        or _scope_is_locally_locked(fallback)
    ):
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
7. search_scope 必须表达“在哪里搜”：
   - exact_place：唯一明确 POI，如“清华大学东门”“北京南站”“某某酒店”。
   - around_anchor：附近/就近，以上一站为锚点。
   - around_route：顺路/路上/找家连锁店或品类店，围绕上一站和下一站找，避免太远。
   - in_area：在某个已命名区域/商场/街区内部或周边找。
   - citywide_ranked / region_ranked：最好吃、评分高、景色好、值得去，更看重评分/匹配度，不强制围绕路线。
8. “麦当劳/KFC/星巴克/超市/便利店/餐厅/咖啡馆”是品牌或品类，不是唯一地点；除非用户给出具体分店名，否则不要 exact_place，通常 around_route。
9. “去最好吃的火锅店”“找个景色好的地方放松”通常 citywide_ranked 或 region_ranked；“顺路去个超市”“附近吃个饭”“找家麦当劳/KFC”通常 around_route 或 around_anchor。
10. radius_m 通常 3000；路线上的餐饮/咖啡不要扩到全城。fallback_radius_m 可为 8000。
11. 只输出 JSON，不要解释。

输出格式：
{
  "raw_need": str,
  "search_category": "dining"|"coffee_tea"|"quiet_rest"|"shopping"|"sightseeing"|"sports"|"generic",
  "search_scope": "exact_place"|"around_anchor"|"around_route"|"in_area"|"citywide_ranked"|"region_ranked",
  "anchor_policy": "none"|"prev"|"next"|"prev_next"|"area",
  "ranking_policy": "relevance"|"distance_then_rating"|"route_detour_then_distance"|"rating_then_relevance"|"vibe_then_rating",
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
- 路线锚点：{anchor_context}
- 锚点坐标：{anchors}

说明：路线锚点中的 role=previous 表示上一站，role=next 表示下一站，role=area 表示命名区域中心。若用户说“附近/顺路/路上/找家品牌或品类店”，应优先 around_anchor 或 around_route，不要输出 region_ranked。

请输出地图搜索意图 JSON。"""


async def build_map_search_intent(
    task: Optional[Task],
    raw_need: str,
    category_hint: Optional[str],
    city: str,
    mode: str,
    anchors: list[list[float]] | None = None,
    anchor_context: list[dict] | None = None,
    place: Optional[str] = None,
) -> MapSearchIntent:
    fallback = _local_search_intent(task, raw_need, category_hint, mode)
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
        anchor_context=json.dumps(anchor_context or [], ensure_ascii=False),
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
