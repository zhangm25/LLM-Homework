import math
import re

from app.schemas import DeviceLocation, Intent, Poi, RouteLeg, TravelPlan, TravelPlanRequest, TravelPlanResponse


KNOWN_DESTINATIONS = {
    "北京南站": {
        "name": "北京南站",
        "category": "Railway station",
        "address": "北京市丰台区永外大街",
        "latitude": 39.865246,
        "longitude": 116.378517,
    },
    "beijing south railway station": {
        "name": "Beijing South Railway Station",
        "category": "Railway station",
        "address": "Fengtai District, Beijing",
        "latitude": 39.865246,
        "longitude": 116.378517,
    },
    "五棵松": {
        "name": "五棵松",
        "category": "Concert venue",
        "address": "北京市海淀区复兴路69号",
        "latitude": 39.910278,
        "longitude": 116.280556,
    },
    "华熙live五棵松": {
        "name": "华熙LIVE·五棵松",
        "category": "Concert venue",
        "address": "北京市海淀区复兴路69号",
        "latitude": 39.910278,
        "longitude": 116.280556,
    },
    "奥森公园": {
        "name": "奥林匹克森林公园",
        "category": "Park",
        "address": "北京市朝阳区科荟路33号",
        "latitude": 40.016667,
        "longitude": 116.391389,
    },
    "奥林匹克森林公园": {
        "name": "奥林匹克森林公园",
        "category": "Park",
        "address": "北京市朝阳区科荟路33号",
        "latitude": 40.016667,
        "longitude": 116.391389,
    },
}


def build_mock_plan(payload: TravelPlanRequest) -> TravelPlanResponse:
    query = payload.query.strip()
    intent = _parse_mock_intent(query, has_location=payload.current_location is not None)

    if payload.current_location is not None:
        return _build_current_location_mock_plan(payload, intent)

    start_place = _format_start_place(payload)

    relaxed_plan = TravelPlan(
        title=f"{intent.destination} Relaxed Day Route",
        score=0.92,
        summary="A low-effort first-visit route covering nature, culture, and a simple dinner area.",
        pois=[
            Poi(
                name="West Lake Broken Bridge",
                category="Nature",
                address="Beishan Street, Xihu District",
                stay_minutes=60,
                reason="A recognizable scenic start with short walking distance and clear city context.",
            ),
            Poi(
                name="Zhejiang Museum",
                category="Culture",
                address="Gushan Road, Xihu District",
                stay_minutes=75,
                reason="Indoor stop that adds local history while keeping the pace comfortable.",
            ),
            Poi(
                name="Hubin Pedestrian Street",
                category="Food and shopping",
                address="Hubin Road, Shangcheng District",
                stay_minutes=90,
                reason="Convenient dinner and return options make it a practical final stop.",
            ),
        ],
        route=[
            RouteLeg(from_place=start_place, to="West Lake Broken Bridge", transport="Taxi", duration_minutes=20),
            RouteLeg(from_place="West Lake Broken Bridge", to="Zhejiang Museum", transport="Walk", duration_minutes=15),
            RouteLeg(from_place="Zhejiang Museum", to="Hubin Pedestrian Street", transport="Bus or taxi", duration_minutes=25),
        ],
        explanation="This plan reduces transfers and long walks while connecting scenery, culture, and dining.",
    )

    food_plan = TravelPlan(
        title=f"{intent.destination} Local Street Route",
        score=0.84,
        summary="A food-and-neighborhood route with fewer landmarks and more flexible stay time.",
        pois=[
            Poi(
                name="Hefang Street",
                category="Historic street",
                address="Hefang Street, Shangcheng District",
                stay_minutes=80,
                reason="Dense food and shop options make it good for casual exploration.",
            ),
            Poi(
                name="Southern Song Imperial Street",
                category="Historic street",
                address="Zhongshan Middle Road, Shangcheng District",
                stay_minutes=70,
                reason="Close to Hefang Street, so the route stays simple.",
            ),
            Poi(
                name="Hubin Intime",
                category="Food and shopping",
                address="Yan'an Road, Shangcheng District",
                stay_minutes=90,
                reason="Reliable restaurant choices make it a stable evening endpoint.",
            ),
        ],
        route=[
            RouteLeg(from_place=start_place, to="Hefang Street", transport="Taxi", duration_minutes=25),
            RouteLeg(from_place="Hefang Street", to="Southern Song Imperial Street", transport="Walk", duration_minutes=10),
            RouteLeg(from_place="Southern Song Imperial Street", to="Hubin Intime", transport="Metro or taxi", duration_minutes=20),
        ],
        explanation="This option trades some scenic coverage for stronger food and neighborhood experience.",
    )

    return TravelPlanResponse(intent=intent, plans=[relaxed_plan, food_plan])


def _build_current_location_mock_plan(payload: TravelPlanRequest, intent: Intent) -> TravelPlanResponse:
    location = payload.current_location
    if location is None:
        raise ValueError("Current location is required for location-aware mock plans.")

    destination = _extract_destination(payload.query, location)
    if destination is not None:
        return _build_destination_mock_plan(payload, intent, destination)

    steps = _mock_steps_from_query(payload.query)
    compact_pois = [
        _build_mock_poi(location, step, index, variant=0)
        for index, step in enumerate(steps, start=1)
    ]
    scenic_pois = [
        _build_mock_poi(location, step, index, variant=1)
        for index, step in enumerate(steps, start=1)
    ]

    start_place = _format_start_place(payload)
    compact_plan = _build_location_plan(
        title="Current Location Compact Route",
        score=0.9,
        summary="A reproducible fallback route generated around your browser location.",
        start_place=start_place,
        start_location=location,
        pois=compact_pois,
        explanation=(
            "The backend did not use a real AMap route for this response, so mock POIs "
            "were generated near your current coordinates instead of using fixed Hangzhou examples."
        ),
    )
    scenic_plan = _build_location_plan(
        title="Current Location Alternative Route",
        score=0.82,
        summary="A second nearby fallback route with slightly wider spacing between stops.",
        start_place=start_place,
        start_location=location,
        pois=scenic_pois,
        explanation=(
            "This alternative keeps the demo runnable without API keys while preserving your current "
            "location as the route origin."
        ),
    )

    return TravelPlanResponse(
        intent=Intent(
            destination="Current location",
            duration=intent.duration,
            preferences=intent.preferences,
        ),
        plans=[compact_plan, scenic_plan],
    )


def _build_destination_mock_plan(
    payload: TravelPlanRequest,
    intent: Intent,
    destination: dict[str, str | float],
) -> TravelPlanResponse:
    location = payload.current_location
    if location is None:
        raise ValueError("Current location is required for destination-aware mock plans.")

    task_steps = _mock_steps_from_query(_query_without_destination(payload.query, str(destination["name"])))
    intermediate_steps = [step for step in task_steps if step["category"] != "General"]

    transit_pois = [
        _build_between_poi(location, destination, step, index, len(intermediate_steps), variant=0)
        for index, step in enumerate(intermediate_steps, start=1)
    ]
    taxi_pois = [
        _build_between_poi(location, destination, step, index, len(intermediate_steps), variant=1)
        for index, step in enumerate(intermediate_steps, start=1)
    ]

    transit_destination = _destination_poi(destination, "Reached as the final stop after the planned errands.")
    taxi_destination = _destination_poi(destination, "Reached directly after the optional on-the-way stop.")

    start_place = _format_start_place(payload)
    transit_plan = _build_location_plan(
        title=f"Transit-first Route to {destination['name']}",
        score=0.9,
        summary=f"Start from your current location, handle the on-the-way task, then continue to {destination['name']}.",
        start_place=start_place,
        start_location=location,
        pois=[*transit_pois, transit_destination],
        explanation=(
            "This fallback route keeps the requested destination as the final stop. "
            "Use the transit-first option when you prefer metro or bus for the longer leg, "
            "and taxi only for short transfers or time-sensitive segments."
        ),
        transport_preference="transit",
    )
    taxi_plan = _build_location_plan(
        title=f"Taxi Route to {destination['name']}",
        score=0.82,
        summary=f"Use taxi-first transfers from your current location to the on-the-way stop and then {destination['name']}.",
        start_place=start_place,
        start_location=location,
        pois=[*taxi_pois, taxi_destination],
        explanation=(
            "This option is simpler when carrying luggage or when metro transfers are inconvenient. "
            "It is a mock fallback estimate, not a live AMap traffic quote."
        ),
        transport_preference="taxi",
    )

    return TravelPlanResponse(
        intent=Intent(
            destination=str(destination["name"]),
            duration=intent.duration,
            preferences=[*intent.preferences, "destination routing"],
        ),
        plans=[transit_plan, taxi_plan],
    )


def _build_location_plan(
    title: str,
    score: float,
    summary: str,
    start_place: str,
    start_location: DeviceLocation,
    pois: list[Poi],
    explanation: str,
    transport_preference: str = "walk",
) -> TravelPlan:
    route = []
    previous_name = start_place
    previous_latitude = start_location.latitude
    previous_longitude = start_location.longitude

    for poi in pois:
        distance_meters = _distance_meters(
            previous_latitude,
            previous_longitude,
            poi.latitude or previous_latitude,
            poi.longitude or previous_longitude,
        )
        route.append(
            RouteLeg(
                from_place=previous_name,
                to=poi.name,
                transport=_transport_label(distance_meters, transport_preference, poi.category),
                duration_minutes=_duration_minutes(distance_meters, transport_preference),
                distance_meters=distance_meters,
            )
        )
        previous_name = poi.name
        previous_latitude = poi.latitude or previous_latitude
        previous_longitude = poi.longitude or previous_longitude

    return TravelPlan(
        title=title,
        score=score,
        summary=summary,
        pois=pois,
        route=route,
        explanation=explanation,
    )


def _mock_steps_from_query(query: str) -> list[dict[str, str]]:
    parts = [
        part.strip()
        for part in re.split(r"\+|->|,|;|\uff0c|\u3001|\uff1b|\u7136\u540e|\u518d|\u548c", query)
        if part.strip()
    ]
    if not parts:
        parts = [query.strip() or "nearby place"]

    return [_classify_mock_step(part) for part in parts[:4]]


def _query_without_destination(query: str, destination_name: str) -> str:
    cleaned = query.replace(destination_name, "")
    cleaned = re.sub(r"(规划|计划|路线|前往|到|去|抵达|终点|目的地|顺路|路上|途中|我要|我想|帮我)", " ", cleaned)
    return cleaned.strip() or "direct destination"


def _classify_mock_step(text: str) -> dict[str, str]:
    normalized = text.lower()
    if _contains_any(normalized, ["eat", "meal", "restaurant", "food", "\u5403\u996d", "\u5403", "\u7f8e\u98df"]):
        return {"name": "Nearby Dining Stop", "category": "Food", "stay": "45", "reason": "Matches the eating task in your request."}
    if _contains_any(normalized, ["basketball", "badminton", "ball", "sport", "\u6253\u7403", "\u7bee\u7403", "\u7fbd\u6bdb\u7403"]):
        return {"name": "Nearby Sports Court", "category": "Sports", "stay": "60", "reason": "Matches the sports task in your request."}
    if _contains_any(normalized, ["coffee", "cafe", "\u5496\u5561"]):
        return {"name": "Nearby Coffee Stop", "category": "Cafe", "stay": "35", "reason": "Matches the coffee task in your request."}
    if _contains_any(normalized, ["library", "study", "\u56fe\u4e66\u9986", "\u5b66\u4e60"]):
        return {"name": "Nearby Study Space", "category": "Study", "stay": "75", "reason": "Matches the study or library task in your request."}
    if _contains_any(normalized, ["package", "parcel", "delivery", "pickup", "\u5feb\u9012", "\u53d6\u4ef6", "\u53d6\u5feb\u9012"]):
        return {"name": "Nearby Package Pickup", "category": "Errand", "stay": "15", "reason": "Matches the package pickup task in your request."}
    if _contains_any(normalized, ["dorm", "home", "return", "\u5bbf\u820d", "\u56de\u5bb6", "\u56de"]):
        return {"name": "Return Destination", "category": "Return", "stay": "5", "reason": "Represents the final return task in your request."}
    return {"name": "Nearby Task Stop", "category": "General", "stay": "30", "reason": "Represents an unclear task near your current location."}


def _build_mock_poi(location: DeviceLocation, step: dict[str, str], index: int, variant: int) -> Poi:
    latitude, longitude = _offset_coordinate(location.latitude, location.longitude, index, variant)
    suffix = "" if variant == 0 else " Alt"
    return Poi(
        name=f"{step['name']}{suffix}",
        category=step["category"],
        address=f"Mock nearby point {index} around current coordinates",
        stay_minutes=int(step["stay"]),
        reason=step["reason"],
        latitude=latitude,
        longitude=longitude,
        source="mock-current-location",
    )


def _build_between_poi(
    location: DeviceLocation,
    destination: dict[str, str | float],
    step: dict[str, str],
    index: int,
    total_steps: int,
    variant: int,
) -> Poi:
    destination_latitude = float(destination["latitude"])
    destination_longitude = float(destination["longitude"])
    fraction = index / (total_steps + 1)
    latitude = location.latitude + (destination_latitude - location.latitude) * fraction
    longitude = location.longitude + (destination_longitude - location.longitude) * fraction

    # A tiny deterministic offset keeps alternative mock stops visually distinct on the map.
    latitude += 0.0010 if variant == 0 else -0.0010
    longitude += -0.0010 if variant == 0 else 0.0010

    suffix = "" if variant == 0 else " Alt"
    name = f"{step['name']}{suffix}"
    if step["category"] == "Food":
        name = f"On-the-way Dining Stop before {destination['name']}{suffix}"

    return Poi(
        name=name,
        category=step["category"],
        address=f"Mock on-the-way point between your current location and {destination['name']}",
        stay_minutes=int(step["stay"]),
        reason=f"{step['reason']} Placed between your current location and {destination['name']}.",
        latitude=round(latitude, 6),
        longitude=round(longitude, 6),
        source="mock-destination-route",
    )


def _destination_poi(destination: dict[str, str | float], reason: str) -> Poi:
    return Poi(
        name=str(destination["name"]),
        category=str(destination["category"]),
        address=str(destination["address"]),
        stay_minutes=5,
        reason=reason,
        latitude=float(destination["latitude"]),
        longitude=float(destination["longitude"]),
        source="mock-destination",
    )


def _offset_coordinate(latitude: float, longitude: float, index: int, variant: int) -> tuple[float, float]:
    base_offsets = [
        (0.0012, 0.0010),
        (0.0020, -0.0014),
        (-0.0015, -0.0020),
        (-0.0022, 0.0016),
    ]
    wide_offsets = [
        (0.0020, 0.0016),
        (0.0030, -0.0022),
        (-0.0024, -0.0030),
        (-0.0034, 0.0024),
    ]
    offsets = wide_offsets if variant == 1 else base_offsets
    latitude_offset, longitude_offset = offsets[(index - 1) % len(offsets)]
    return round(latitude + latitude_offset, 6), round(longitude + longitude_offset, 6)


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


def _duration_minutes(distance_meters: int, transport_preference: str) -> int:
    if transport_preference == "taxi":
        return max(8, round(distance_meters / 420))
    if transport_preference == "transit":
        return max(10, round(distance_meters / 320) + 8)
    return max(3, round(distance_meters / 75))


def _transport_label(distance_meters: int, transport_preference: str, category: str) -> str:
    if transport_preference == "taxi":
        return "Taxi"
    if transport_preference == "transit":
        if category == "Food" and distance_meters < 1200:
            return "Walk or short taxi"
        if distance_meters >= 2500:
            return "Metro or bus + short walk"
        return "Bus, bike, or taxi"
    return "Walk"


def _extract_destination(query: str, location: DeviceLocation) -> dict[str, str | float] | None:
    lowered = query.lower()
    for alias, destination in KNOWN_DESTINATIONS.items():
        if alias in lowered or alias in query:
            return destination

    match = re.search(r"(?:到|去|前往|抵达|目的地是|终点是)\s*([^，,。；;\n]+)", query)
    if not match:
        match = re.search(r"\bto\s+([^,.;\n]+)", query, flags=re.IGNORECASE)
    if not match:
        return None

    name = _clean_destination_name(match.group(1))
    if not name:
        return None

    latitude, longitude = _offset_coordinate(location.latitude, location.longitude, index=4, variant=1)
    return {
        "name": name,
        "category": "Destination",
        "address": "Mock destination inferred from the user request",
        "latitude": latitude,
        "longitude": longitude,
    }


def _clean_destination_name(raw_name: str) -> str:
    name = re.split(r"(顺路|顺便|路上|途中|然后|再|并且|同时|看|跑步|吃|喝|打|取|学习)", raw_name, maxsplit=1)[0]
    name = re.sub(r"^(一下|一个|去|到|前往|抵达)+", "", name.strip())
    return name.strip(" ，,。；;")


def _parse_mock_intent(query: str, has_location: bool = False) -> Intent:
    normalized = query.lower()
    destination = "Current area" if has_location else "Hangzhou"
    if _contains_any(normalized, ["shanghai", "\u4e0a\u6d77"]):
        destination = "Shanghai"
    elif _contains_any(normalized, ["beijing", "\u5317\u4eac"]):
        destination = "Beijing"
    elif _contains_any(normalized, ["nanjing", "\u5357\u4eac"]):
        destination = "Nanjing"
    elif _contains_any(normalized, ["hangzhou", "\u676d\u5dde"]):
        destination = "Hangzhou"

    preferences = []
    if _contains_any(normalized, ["nature", "view", "lake", "park", "\u81ea\u7136", "\u98ce\u666f", "\u6e56", "\u516c\u56ed"]):
        preferences.append("nature views")
    if _contains_any(normalized, ["food", "eat", "snack", "restaurant", "\u7f8e\u98df", "\u5403", "\u5c0f\u5403", "\u9910\u5385"]):
        preferences.append("food")
    if _contains_any(normalized, ["relaxed", "easy", "slow", "not tiring", "\u8f7b\u677e", "\u4e0d\u7d2f", "\u6162"]):
        preferences.append("low effort")
    if not preferences:
        preferences = ["classic sights", "smooth routing"]

    duration = "one day" if _contains_any(normalized, ["one day", "1 day", "day trip", "\u4e00\u5929", "1\u5929", "\u4e00\u65e5"]) else "half day to one day"

    return Intent(destination=destination, duration=duration, preferences=preferences)


def _format_start_place(payload: TravelPlanRequest) -> str:
    if payload.current_location is None:
        return "Hotel or start point"

    location = payload.current_location
    if location.label:
        return location.label

    accuracy = ""
    if location.accuracy_meters is not None:
        accuracy = f", accuracy {round(location.accuracy_meters)}m"
    return f"Current location ({location.latitude:.5f}, {location.longitude:.5f}{accuracy})"


def _contains_any(text: str, candidates: list[str]) -> bool:
    return any(candidate in text for candidate in candidates)
