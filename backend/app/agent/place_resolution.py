"""Place-first resolution pass.

This is Phase A of the two-stage design: build place slots, query AMap, pick a
default candidate, and write the selected coordinates back into the existing
IntentObject so the current scheduler can keep doing time/route planning.
"""

from __future__ import annotations

from typing import Optional

from ..llm.vocab import TASK_DEFAULT_KEYWORD
from ..models.intent import ExplicitPOI, IntentObject, Task
from ..models.place import PlaceCandidate, PlaceResolution, PlaceSlot
from ..planner.scheduler import (
    CITY_CENTER,
    DEFAULT_CITY,
    _clean_intent,
    _grounding_targets,
    _place_should_be_main_destination,
    _resolve_in_area,
    _resolve_named,
    _resolve_near,
    _venue_category,
)
from ..tools.amap_client import POI
from .place_norm import normalize_place_name


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
    pois = await _resolve_named(slot.query, slot.city)
    slot.candidates = [_candidate(p) for p in pois[:5]]
    slot.selected = slot.candidates[0] if slot.candidates else None
    slot.status = _slot_status(slot.candidates)
    slot.needs_user_choice = len(slot.candidates) > 1
    return slot


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

    prev_loc = start.location or origin or CITY_CENTER.get(city, CITY_CENTER[DEFAULT_CITY])
    for place, task in _grounding_targets(intent):
        category = _venue_category(task)
        if place and category and not _place_should_be_main_destination(place):
            raw = await _resolve_in_area(place, category, city)
            query = f"{place} {category}"
            role = "activity_poi"
        elif place:
            raw = await _resolve_named(place, city)
            query = place
            role = "waypoint"
        else:
            query = (
                category
                or (_clean_intent(task.intent) if task and task.intent else "")
                or TASK_DEFAULT_KEYWORD.get(task.type if task else "other", "地点")
            )
            raw = await _resolve_near(query, prev_loc, city)
            role = "activity_poi"

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
            prev_loc = slot.selected.location
        slots.append(slot)

    resolved = sum(1 for s in slots if s.status == "selected")
    status = "places_ready" if slots and resolved == len(slots) else "partial" if resolved else "unresolved"
    return PlaceResolution(status=status, slots=slots)
