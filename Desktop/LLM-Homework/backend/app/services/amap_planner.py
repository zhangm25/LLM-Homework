import math
import re
from itertools import product

from app.schemas import Poi, RouteLeg, TravelPlan, TravelPlanRequest, TravelPlanResponse
from app.services.amap_client import (
    AMapPlace,
    convert_gps_to_amap,
    driving_route,
    geocode_place,
    reverse_geocode,
    search_places_around,
    transit_route,
    walking_route,
)
from app.services.intent_parser import parse_user_intent
from app.services.plan_ranker import rank_candidate_plans
from app.services.task_planner import build_task_plan
from app.schemas import CandidateRoutePlan, Intent


KNOWN_DESTINATION_PLACES = {
    "北京南站": AMapPlace(
        name="北京南站",
        type_name="Railway station",
        address="北京市丰台区永外大街",
        latitude=39.865246,
        longitude=116.378517,
    ),
    "华熙LIVE·五棵松": AMapPlace(
        name="华熙LIVE·五棵松",
        type_name="Concert venue",
        address="北京市海淀区复兴路69号",
        latitude=39.910278,
        longitude=116.280556,
    ),
    "奥林匹克森林公园": AMapPlace(
        name="奥林匹克森林公园",
        type_name="Park",
        address="北京市朝阳区科荟路33号",
        latitude=40.016667,
        longitude=116.391389,
    ),
}


def build_amap_plan(payload: TravelPlanRequest) -> TravelPlanResponse | None:
    if payload.current_location is None:
        return None

    location = payload.current_location
    amap_latitude, amap_longitude = convert_gps_to_amap(location.latitude, location.longitude)

    destination_query = _extract_destination_query(payload.query)
    if destination_query:
        destination = KNOWN_DESTINATION_PLACES.get(destination_query)
        if destination is None:
            destination = geocode_place(destination_query, _city_for_destination(destination_query))
        if destination is not None:
            destination_plan = _build_destination_plan(
                payload=payload,
                start_label=_start_label(amap_latitude, amap_longitude),
                start_coordinate=(amap_latitude, amap_longitude),
                destination=destination,
            )
            if destination_plan is not None:
                return destination_plan

    intent_result = parse_user_intent(payload.query)
    task_plan = build_task_plan(intent_result)

    task_candidates = []
    for task in task_plan.tasks:
        keywords = _keywords_for_task(task.candidate_poi_types)
        places = search_places_around(amap_latitude, amap_longitude, keywords)
        if not places:
            return None
        task_candidates.append((task.action, places[:2]))

    combinations = list(product(*[places for _, places in task_candidates]))[:4]
    if not combinations:
        return None

    start_label = _start_label(location.latitude, location.longitude)
    plans = []
    rank_inputs = []
    for index, places in enumerate(combinations, start=1):
        plan = _build_plan_for_places(index, payload, start_label, (amap_latitude, amap_longitude), list(places))
        if plan is None:
            continue
        plans.append(plan)
        rank_inputs.append(
            CandidateRoutePlan(
                id=str(index),
                title=plan.title,
                total_duration_minutes=_total_duration(plan),
                detour_distance_km=_total_distance(plan) / 1000,
                poi_type_match=0.9,
                matched_preferences=plan.intent_preferences if hasattr(plan, "intent_preferences") else [],
                poi_types=[poi.category for poi in plan.pois],
            )
        )

    if not plans:
        return None

    ranked = rank_candidate_plans(rank_inputs, _preferences_from_query(payload.query)).ranked_plans
    rank_by_title = {item.title: item for item in ranked}
    plans.sort(key=lambda item: rank_by_title.get(item.title).rank if item.title in rank_by_title else 999)
    for plan in plans:
        ranked_plan = rank_by_title.get(plan.title)
        if ranked_plan:
            plan.score = ranked_plan.score
            plan.explanation = (
                f"AMap route ranked this plan using total time, walking distance, POI type match, "
                f"and preference match. {ranked_plan.ranking_reason}"
            )

    return TravelPlanResponse(
        intent=Intent(
            destination=start_label,
            duration="multi-stop route",
            preferences=_preferences_from_query(payload.query) or ["nearby POIs", "shorter route"],
        ),
        plans=plans,
    )


def _build_destination_plan(
    payload: TravelPlanRequest,
    start_label: str,
    start_coordinate: tuple[float, float],
    destination: AMapPlace,
) -> TravelPlanResponse | None:
    intermediate_places = []
    if _has_food_task(payload.query):
        midpoint = (
            (start_coordinate[0] + destination.latitude) / 2,
            (start_coordinate[1] + destination.longitude) / 2,
        )
        intermediate_places = search_places_around(
            midpoint[0],
            midpoint[1],
            ["餐厅", "美食", "饭店", "restaurant", "meal"],
            radius_meters=4000,
            limit=2,
        )
        if not intermediate_places:
            return None

    place_chains = []
    if intermediate_places:
        place_chains = [[place, destination] for place in intermediate_places]
    else:
        place_chains = [[destination]]

    plans = []
    first_chain = place_chains[0]
    route_city = _city_for_destination(destination.name) or _city_for_destination(destination.address) or "北京"
    transit_plan = _build_route_plan(
        title=f"Transit-first Route to {destination.name}",
        score=0.93,
        summary=f"Transit-oriented route from your current location to {destination.name}"
        + (" with an on-the-way dining stop." if intermediate_places else "."),
        start_label=start_label,
        start_coordinate=start_coordinate,
        places=first_chain,
        route_mode="transit",
        city=route_city,
    )
    if transit_plan is not None:
        plans.append(transit_plan)

    taxi_plan = _build_route_plan(
        title=f"AMap Taxi Route to {destination.name}",
        score=0.86,
        summary=f"Taxi-style route from your current location to {destination.name}"
        + (" with an on-the-way dining stop." if intermediate_places else "."),
        start_label=start_label,
        start_coordinate=start_coordinate,
        places=first_chain,
        route_mode="taxi",
        city=route_city,
    )
    if taxi_plan is not None:
        plans.append(taxi_plan)

    if len(place_chains) > 1:
        alternative_plan = _build_route_plan(
            title=f"Alternative Dining Route to {destination.name}",
            score=0.8,
            summary=f"Alternative AMap dining stop before continuing to {destination.name}.",
            start_label=start_label,
            start_coordinate=start_coordinate,
            places=place_chains[1],
            route_mode="taxi",
            city=route_city,
        )
        if alternative_plan is not None:
            plans.append(alternative_plan)

    if not plans:
        return None

    return TravelPlanResponse(
        intent=Intent(
            destination=destination.name,
            duration="destination route",
            preferences=_preferences_from_query(payload.query) or ["on-the-way stop", "destination routing"],
        ),
        plans=plans,
    )


def _build_route_plan(
    title: str,
    score: float,
    summary: str,
    start_label: str,
    start_coordinate: tuple[float, float],
    places: list[AMapPlace],
    route_mode: str,
    city: str | None = None,
) -> TravelPlan | None:
    route = []
    pois = []
    previous_name = start_label
    previous_coordinate = start_coordinate

    for index, place in enumerate(places):
        is_destination = index == len(places) - 1
        if route_mode == "taxi":
            route_result = driving_route(previous_coordinate, (place.latitude, place.longitude))
        elif route_mode == "transit" and city:
            route_result = transit_route(previous_coordinate, (place.latitude, place.longitude), city)
        else:
            route_result = None
        distance_meters = (
            route_result.distance_meters
            if route_result is not None
            else _distance_meters(previous_coordinate[0], previous_coordinate[1], place.latitude, place.longitude)
        )
        route.append(
            RouteLeg(
                from_place=previous_name,
                to=place.name,
                transport=_transport_label(route_mode, distance_meters, is_destination),
                duration_minutes=(
                    route_result.duration_minutes
                    if route_result is not None
                    else _estimated_duration_minutes(route_mode, distance_meters)
                ),
                distance_meters=distance_meters,
                polyline=route_result.polyline if route_result is not None else None,
                steps=list(route_result.steps) if route_result is not None else [],
            )
        )
        pois.append(
            Poi(
                name=place.name,
                category=place.type_name,
                address=place.address,
                stay_minutes=5 if is_destination else 45,
                reason=(
                    "Final destination resolved by AMap geocoding."
                    if is_destination
                    else "Selected from AMap POI search near the path from your current location to the destination."
                ),
                latitude=place.latitude,
                longitude=place.longitude,
                source="amap",
            )
        )
        previous_name = place.name
        previous_coordinate = (place.latitude, place.longitude)

    explanation = (
        "This plan keeps the requested destination as the final stop and inserts the dining POI before it. "
        "Taxi options use AMap driving route data; transit-first options use AMap public transit route data "
        "including walking segments, bus or metro line names, stops, and transfer details when available."
    )
    return TravelPlan(
        title=title,
        score=score,
        summary=summary,
        pois=pois,
        route=route,
        explanation=explanation,
    )


def _build_plan_for_places(
    index: int,
    payload: TravelPlanRequest,
    start_label: str,
    start_coordinate: tuple[float, float],
    places: list[AMapPlace],
) -> TravelPlan | None:
    route = []
    pois = []
    previous_name = start_label
    previous_coordinate = start_coordinate

    for place in places:
        route_result = walking_route(previous_coordinate, (place.latitude, place.longitude))
        if route_result is None:
            return None
        route.append(
            RouteLeg(
                from_place=previous_name,
                to=place.name,
                transport="AMap walking",
                duration_minutes=route_result.duration_minutes,
                distance_meters=route_result.distance_meters,
                polyline=route_result.polyline,
            )
        )
        pois.append(
            Poi(
                name=place.name,
                category=place.type_name,
                address=place.address,
                stay_minutes=45,
                reason="Selected from AMap nearby POI search for the parsed task chain.",
                latitude=place.latitude,
                longitude=place.longitude,
                source="amap",
            )
        )
        previous_name = place.name
        previous_coordinate = (place.latitude, place.longitude)

    return TravelPlan(
        title=f"AMap Route Option {index}",
        score=0.0,
        summary=f"Real AMap-backed route with {len(places)} stops from your current location.",
        pois=pois,
        route=route,
        explanation="Generated from AMap POI search and walking route planning.",
    )


def _keywords_for_task(candidate_types) -> list[str]:
    keywords = []
    for candidate_type in candidate_types:
        keywords.extend(candidate_type.keywords)
        keywords.append(candidate_type.type.replace("_", " "))
    return keywords[:6]


def _start_label(latitude: float, longitude: float) -> str:
    address = reverse_geocode(latitude, longitude)
    if address:
        return address
    return f"Current location ({latitude:.5f}, {longitude:.5f})"


def _total_duration(plan: TravelPlan) -> int:
    return sum(leg.duration_minutes for leg in plan.route) + sum(poi.stay_minutes for poi in plan.pois)


def _total_distance(plan: TravelPlan) -> int:
    return sum(leg.distance_meters or 0 for leg in plan.route)


def _preferences_from_query(query: str) -> list[str]:
    lowered = query.lower()
    preferences = []
    if any(token in lowered for token in ["short", "fast", "near", "\u8fd1", "\u5feb"]):
        preferences.append("shorter route")
    if any(token in lowered for token in ["quiet", "study", "\u5b89\u9759", "\u5b66\u4e60"]):
        preferences.append("quiet")
    if any(token in lowered for token in ["food", "eat", "\u5403", "\u7f8e\u98df"]):
        preferences.append("food")
    return preferences


def _extract_destination_query(query: str) -> str | None:
    if "北京南站" in query:
        return "北京南站"
    if "五棵松" in query:
        return "华熙LIVE·五棵松"
    if "奥森公园" in query:
        return "奥林匹克森林公园"
    if "奥林匹克森林公园" in query:
        return "奥林匹克森林公园"
    if "beijing south" in query.lower():
        return "Beijing South Railway Station"

    match = re.search(r"(?:到|去|前往|抵达|目的地是|终点是)\s*([^，,。；;\n]+)", query)
    if not match:
        match = re.search(r"\bto\s+([^,.;\n]+)", query, flags=re.IGNORECASE)
    if not match:
        return None

    destination = re.split(r"(顺路|顺便|路上|途中|然后|再|并且|同时|看|跑步|吃|喝|打|取|学习)", match.group(1), maxsplit=1)[0]
    destination = destination.strip(" ，,。；;")
    return destination or None


def _city_for_destination(destination: str) -> str | None:
    if (
        "北京" in destination
        or "beijing" in destination.lower()
        or "五棵松" in destination
        or "奥林匹克森林公园" in destination
    ):
        return "北京"
    if "上海" in destination or "shanghai" in destination.lower():
        return "上海"
    return None


def _has_food_task(query: str) -> bool:
    lowered = query.lower()
    return any(token in lowered for token in ["吃", "吃饭", "美食", "餐厅", "饭店", "food", "eat", "restaurant", "meal"])


def _transport_label(route_mode: str, distance_meters: int, is_destination: bool) -> str:
    if route_mode == "taxi":
        return "AMap taxi/driving"
    if distance_meters >= 2500 or is_destination:
        return "Metro or bus + short walk"
    return "Walk, bike, or short taxi"


def _estimated_duration_minutes(route_mode: str, distance_meters: int) -> int:
    if route_mode == "taxi":
        return max(8, round(distance_meters / 420))
    return max(10, round(distance_meters / 320) + 8)


def _distance_meters(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> int:
    radius_meters = 6371000
    start_phi = math.radians(start_latitude)
    end_phi = math.radians(end_latitude)
    delta_phi = math.radians(end_latitude - start_latitude)
    delta_lambda = math.radians(end_longitude - start_longitude)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(start_phi) * math.cos(end_phi) * math.sin(delta_lambda / 2) ** 2
    )
    return round(radius_meters * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine)))
