"""Place-first resolution pass.

This is Phase A of the two-stage design: build place slots, query AMap, pick a
default candidate, and write the selected coordinates back into the existing
IntentObject so the current scheduler can keep doing time/route planning.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from ..debug_log import write_debug_log
from ..llm.vocab import TASK_DEFAULT_KEYWORD
from ..models.intent import ExplicitPOI, IntentObject, Task
from ..models.place import PlaceCandidate, PlaceResolution, PlaceSlot
from ..planner.scheduler import (
    CITY_CENTER,
    DEFAULT_CITY,
    _clean_intent,
    _extract_hhmm,
    _grounding_targets,
    _parse_clock,
    _place_should_be_main_destination,
    _resolve_in_area,
    _resolve_named,
    _resolve_near,
    _resolve_near_anchors,
    _task_likely_between_fixed,
    _venue_category,
)
from ..tools.amap_client import POI
from .place_norm import normalize_place_name


def _poi_debug(poi: POI) -> dict:
    return {
        "id": poi.id,
        "name": poi.name,
        "address": poi.address,
        "location": poi.location,
        "type": poi.type,
        "distance_m": poi.distance_m,
        "rating": poi.rating,
        "cost": poi.cost,
    }


def _candidate_debug(candidate: PlaceCandidate | None) -> dict | None:
    return candidate.model_dump() if candidate else None


def _log_place_resolution(label: str, **data) -> None:
    safe = {}
    for key, value in data.items():
        if hasattr(value, "model_dump"):
            safe[key] = value.model_dump()
        else:
            safe[key] = value
    write_debug_log(
        f"========== PLACE RESOLUTION [{label}] ==========\n"
        f"{json.dumps(safe, ensure_ascii=False, default=str, indent=2)[:12000]}\n"
        f"========== END PLACE RESOLUTION [{label}] =========="
    )


def _candidate(poi: POI) -> PlaceCandidate:
    return PlaceCandidate(
        id=poi.id or poi.name,
        name=poi.name,
        address=poi.address or "",
        location=poi.location,
        rating=poi.rating,
        cost=poi.cost,
        distance_m=poi.distance_m,
        type=poi.type,
    )


def _slot_status(cands: list[PlaceCandidate]) -> str:
    return "selected" if cands else "unresolved"


def _apply_to_endpoint(slot: PlaceSlot, endpoint) -> None:
    if not slot.selected:
        return
    endpoint.value = slot.selected.name
    endpoint.location = slot.selected.location
    endpoint.source = "nl_extract"
    endpoint.type = "named"


def _apply_to_task(task: Task, selected: PlaceCandidate) -> None:
    task.at = selected.name
    task.location = selected.location
    task.address = selected.address or None


def _apply_to_explicit(explicit: ExplicitPOI, selected: PlaceCandidate) -> None:
    explicit.name = selected.name
    explicit.location = selected.location
    explicit.address = selected.address or None


def _match_explicit(intent: IntentObject, place: str) -> Optional[ExplicitPOI]:
    target = normalize_place_name(place) or place
    for poi in intent.explicit_pois:
        name = normalize_place_name(poi.name) or poi.name
        if name == target or name in target or target in name:
            return poi
    return None


async def _resolve_named_slot(slot: PlaceSlot) -> PlaceSlot:
    _log_place_resolution(
        "search:named_slot:start",
        slot_id=slot.id,
        role=slot.role,
        source_text=slot.source_text,
        query=slot.query,
        city=slot.city,
        strategy="named_text_search",
    )
    pois = await _resolve_named(slot.query, slot.city)
    slot.candidates = [_candidate(p) for p in pois[:5]]
    slot.selected = slot.candidates[0] if slot.candidates else None
    slot.status = _slot_status(slot.candidates)
    slot.needs_user_choice = len(slot.candidates) > 1
    _log_place_resolution(
        "search:named_slot:result",
        slot_id=slot.id,
        role=slot.role,
        query=slot.query,
        candidates=[_poi_debug(p) for p in pois[:5]],
        selected=_candidate_debug(slot.selected),
        status=slot.status,
        needs_user_choice=slot.needs_user_choice,
    )
    return slot


def _is_route_anchored_activity(task: Optional[Task], category: Optional[str]) -> bool:
    if not task:
        return False
    text = f"{task.intent or ''} {category or ''}"
    return task.type == "dining" or any(token in text for token in ("吃", "饭", "餐", "咖啡", "茶", "brunch"))


Target = tuple[Optional[str], Optional[Task]]
ContextKey = tuple[str, int]


def _fixed_meta(ev) -> dict:
    base = datetime.now()
    fs = _parse_clock(_extract_hhmm(ev.start), base) if _extract_hhmm(ev.start) else None
    fe = _parse_clock(_extract_hhmm(ev.end), base) if _extract_hhmm(ev.end) else None
    if fs and fe and fe <= fs:
        fe = None
    return {"start": fs, "end": fe}


def _fixed_anchors(intent: IntentObject) -> list[dict]:
    out = []
    for ev in sorted(intent.fixed_events, key=lambda e: _extract_hhmm(e.start) or "99:99"):
        if ev.location:
            out.append({"label": ev.place, "location": ev.location, "meta": _fixed_meta(ev)})
    return out


def _target_context(task: Optional[Task], fixed: list[dict]) -> ContextKey:
    if not fixed:
        return ("free", 0)
    for i in range(1, len(fixed)):
        if _task_likely_between_fixed(task, fixed[i - 1]["meta"], fixed[i]["meta"]):
            return ("between", i)
    return ("after_fixed", 0)


def _context_start_location(context: ContextKey, fixed: list[dict], default_start: list[float]) -> list[float]:
    kind, index = context
    if kind == "between":
        return fixed[index - 1]["location"]
    if kind == "after_fixed" and fixed:
        return fixed[-1]["location"]
    return default_start


def _context_end_location(context: ContextKey, fixed: list[dict], end_loc: Optional[list[float]]) -> Optional[list[float]]:
    kind, index = context
    if kind == "between":
        return fixed[index]["location"]
    return end_loc


async def _resolve_named_cached(place: str, city: str, cache: dict[str, list[POI]]) -> list[POI]:
    if place not in cache:
        cache[place] = await _resolve_named(place, city)
    return cache[place]


async def _lookahead_anchor(
    targets: list[Target],
    contexts: list[ContextKey],
    start_index: int,
    context: ContextKey,
    city: str,
    boundary: Optional[list[float]],
    named_cache: dict[str, list[POI]],
) -> Optional[list[float]]:
    has_future_same_context = False
    for future_i in range(start_index + 1, len(targets)):
        if contexts[future_i] != context:
            continue
        has_future_same_context = True
        place, task = targets[future_i]
        if task and task.location:
            return task.location
        if place:
            cands = await _resolve_named_cached(place, city, named_cache)
            if cands:
                return cands[0].location
    return None if has_future_same_context else boundary


def _target_debug(place: Optional[str], task: Optional[Task]) -> dict:
    return {
        "place": place,
        "task": task.model_dump() if task else None,
    }


def _unique_anchors(*anchors: Optional[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for anchor in anchors:
        if not anchor:
            continue
        if not any(abs(anchor[0] - old[0]) < 0.000001 and abs(anchor[1] - old[1]) < 0.000001 for old in out):
            out.append(anchor)
    return out


async def resolve_place_slots(
    intent: IntentObject,
    origin: Optional[list[float]],
    city: str,
) -> PlaceResolution:
    """Resolve places and mutate ``intent`` with selected candidate coordinates."""
    slots: list[PlaceSlot] = []
    slot_i = 1

    def next_id() -> str:
        nonlocal slot_i
        value = f"slot-{slot_i}"
        slot_i += 1
        return value

    start = intent.constraints.start
    if start.location:
        slots.append(PlaceSlot(
            id=next_id(),
            role="start",
            source_text=start.value or "当前位置",
            query=start.value or "当前位置",
            city=city,
            status="selected",
            selected=PlaceCandidate(
                id="origin" if start.source == "geolocation" else "start",
                name=start.value or "当前位置",
                address=start.value or "",
                location=start.location,
            ),
            reason="起点已有坐标，直接作为已确定地点。",
        ))
    elif start.type == "current" and origin:
        start.location = origin
        slots.append(PlaceSlot(
            id=next_id(),
            role="start",
            source_text=start.value or "当前位置",
            query=start.value or "当前位置",
            city=city,
            status="selected",
            selected=PlaceCandidate(id="origin", name=start.value or "当前位置", location=origin),
            reason="浏览器当前位置可用，直接作为起点。",
        ))
    elif start.type == "named" and start.value:
        slot = await _resolve_named_slot(PlaceSlot(
            id=next_id(), role="start", source_text=start.value, query=start.value, city=city
        ))
        if slot.selected:
            _apply_to_endpoint(slot, start)
        slots.append(slot)

    end = intent.constraints.end
    if end and end.value and not end.location:
        slot = await _resolve_named_slot(PlaceSlot(
            id=next_id(), role="end", source_text=end.value, query=end.value, city=city
        ))
        if slot.selected:
            _apply_to_endpoint(slot, end)
        slots.append(slot)

    for ev in intent.fixed_events:
        if ev.location:
            continue
        slot = await _resolve_named_slot(PlaceSlot(
            id=next_id(), role="fixed", source_text=ev.place, query=ev.place, city=city
        ))
        if slot.selected:
            ev.place = slot.selected.name
            ev.location = slot.selected.location
        slots.append(slot)

    start_loc = start.location or origin or CITY_CENTER.get(city, CITY_CENTER[DEFAULT_CITY])
    fixed = _fixed_anchors(intent)
    end_loc = intent.constraints.end.location if intent.constraints.end and intent.constraints.end.location else None
    targets = _grounding_targets(intent)
    target_contexts = [_target_context(task, fixed) for _, task in targets]
    context_prev: dict[ContextKey, list[float]] = {}
    named_cache: dict[str, list[POI]] = {}
    _log_place_resolution(
        "start",
        city=city,
        origin=origin,
        initial_start_loc=start_loc,
        fixed_anchors=fixed,
        end_location=end_loc,
        targets=[{**_target_debug(place, task), "context": target_contexts[i]} for i, (place, task) in enumerate(targets)],
    )
    for target_i, (place, task) in enumerate(targets):
        category = _venue_category(task)
        context = target_contexts[target_i]
        prev_loc = context_prev.get(context) or _context_start_location(context, fixed, start_loc)
        if place and category and not _place_should_be_main_destination(place):
            strategy = "area_around_search_first"
            raw = await _resolve_in_area(place, category, city)
            query = f"{place} {category}"
            role = "activity_poi"
            anchors = []
            anchor_note = f"先定位区域「{place}」，再在区域周边搜索「{category}」；无结果才退回文本搜索。"
        elif place:
            strategy = "named_text_search"
            raw = await _resolve_named_cached(place, city, named_cache)
            query = place
            role = "waypoint"
            anchors = []
            anchor_note = "明确地点，按名称检索真实 POI。"
        else:
            query = (
                category
                or (_clean_intent(task.intent) if task and task.intent else "")
                or TASK_DEFAULT_KEYWORD.get(task.type if task else "other", "地点")
            )
            if _is_route_anchored_activity(task, category):
                next_loc = await _lookahead_anchor(
                    targets,
                    target_contexts,
                    target_i,
                    context,
                    city,
                    _context_end_location(context, fixed, end_loc),
                    named_cache,
                )
                anchors = _unique_anchors(prev_loc, next_loc)
                strategy = "route_anchor_around_search"
                anchor_note = "餐饮/咖啡/茶等路线型模糊任务，围绕同一排程上下文中的上一站和下一站锚点周边搜索。"
                raw = await _resolve_near_anchors(query, anchors, city) if anchors else await _resolve_near(query, prev_loc, city)
            else:
                anchors = _unique_anchors(prev_loc)
                strategy = "previous_anchor_around_search"
                anchor_note = "普通模糊活动，围绕上一站周边搜索。"
                raw = await _resolve_near(query, prev_loc, city)
            role = "activity_poi"

        _log_place_resolution(
            "search:activity:result",
            slot_index=target_i + 1,
            role=role,
            source_text=place or (task.intent if task else query) or query,
            query=query,
            category=category,
            city=city,
            strategy=strategy,
            schedule_context=context,
            anchor_location=prev_loc,
            route_anchors=anchors,
            note=anchor_note,
            candidates=[_poi_debug(p) for p in raw[:5]],
        )
        slot = PlaceSlot(
            id=next_id(),
            role=role,
            source_text=place or (task.intent if task else query) or query,
            query=query,
            city=city,
            anchor_location=prev_loc,
            status="unresolved",
            candidates=[_candidate(p) for p in raw[:5]],
            needs_user_choice=len(raw) > 1,
        )
        slot.selected = slot.candidates[0] if slot.candidates else None
        slot.status = _slot_status(slot.candidates)
        if slot.selected:
            if task:
                _apply_to_task(task, slot.selected)
            elif place:
                explicit = _match_explicit(intent, place)
                if explicit:
                    _apply_to_explicit(explicit, slot.selected)
            context_prev[context] = slot.selected.location
        slots.append(slot)
        _log_place_resolution(
            "slot:selected",
            slot=slot,
            selected=_candidate_debug(slot.selected),
            next_prev_loc=prev_loc,
        )

    resolved = sum(1 for s in slots if s.status == "selected")
    status = "places_ready" if slots and resolved == len(slots) else "partial" if resolved else "unresolved"
    resolution = PlaceResolution(status=status, slots=slots)
    _log_place_resolution("done", status=status, resolution=resolution)
    return resolution
