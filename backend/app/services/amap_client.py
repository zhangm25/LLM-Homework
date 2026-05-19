import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


AMAP_REST_BASE_URL = "https://restapi.amap.com"


@dataclass(frozen=True)
class AMapPlace:
    name: str
    type_name: str
    address: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class AMapRoute:
    duration_minutes: int
    distance_meters: int
    polyline: str
    steps: tuple[str, ...] = ()


def geocode_place(address: str, city: str | None = None) -> AMapPlace | None:
    params = {
        "address": address,
        "output": "JSON",
    }
    if city:
        params["city"] = city

    data = _amap_get("/v3/geocode/geo", params)
    geocodes = data.get("geocodes", [])
    if not geocodes:
        return None

    raw_place = geocodes[0]
    location = raw_place.get("location")
    if not isinstance(location, str) or "," not in location:
        return None

    longitude_text, latitude_text = location.split(",", 1)
    try:
        longitude = float(longitude_text)
        latitude = float(latitude_text)
    except ValueError:
        return None

    formatted_address = raw_place.get("formatted_address")
    return AMapPlace(
        name=str(formatted_address or address),
        type_name="Destination",
        address=str(formatted_address or address),
        latitude=latitude,
        longitude=longitude,
    )


def amap_is_configured() -> bool:
    return bool(_get_env("MAP_API_KEY"))


def reverse_geocode(latitude: float, longitude: float) -> str | None:
    data = _amap_get(
        "/v3/geocode/regeo",
        {
            "location": _format_location(longitude, latitude),
            "radius": "1000",
            "extensions": "base",
            "output": "JSON",
        },
    )
    regeocode = data.get("regeocode", {})
    formatted = regeocode.get("formatted_address")
    return formatted if isinstance(formatted, str) and formatted else None


def convert_gps_to_amap(latitude: float, longitude: float) -> tuple[float, float]:
    data = _amap_get(
        "/v3/assistant/coordinate/convert",
        {
            "locations": _format_location(longitude, latitude),
            "coordsys": "gps",
            "output": "JSON",
        },
    )
    locations = data.get("locations")
    if not isinstance(locations, str) or "," not in locations:
        return latitude, longitude

    longitude_text, latitude_text = locations.split(",", 1)
    try:
        return float(latitude_text), float(longitude_text)
    except ValueError:
        return latitude, longitude


def search_places_around(
    latitude: float,
    longitude: float,
    keywords: list[str],
    radius_meters: int = 3000,
    limit: int = 3,
) -> list[AMapPlace]:
    places: list[AMapPlace] = []
    seen: set[str] = set()

    for keyword in keywords:
        data = _amap_get(
            "/v3/place/around",
            {
                "location": _format_location(longitude, latitude),
                "keywords": keyword,
                "radius": str(radius_meters),
                "offset": str(limit),
                "page": "1",
                "extensions": "base",
                "output": "JSON",
            },
        )
        for raw_poi in data.get("pois", []):
            place = _parse_place(raw_poi)
            if place is None or place.name in seen:
                continue
            places.append(place)
            seen.add(place.name)
            if len(places) >= limit:
                return places

    return places


def walking_route(origin: tuple[float, float], destination: tuple[float, float]) -> AMapRoute | None:
    origin_latitude, origin_longitude = origin
    destination_latitude, destination_longitude = destination
    data = _amap_get(
        "/v3/direction/walking",
        {
            "origin": _format_location(origin_longitude, origin_latitude),
            "destination": _format_location(destination_longitude, destination_latitude),
            "output": "JSON",
        },
    )

    paths = data.get("route", {}).get("paths", [])
    if not paths:
        return None

    path = paths[0]
    duration_seconds = _to_int(path.get("duration"), default=0)
    distance_meters = _to_int(path.get("distance"), default=0)
    polylines = []
    for step in path.get("steps", []):
        polyline = step.get("polyline")
        if isinstance(polyline, str) and polyline:
            polylines.append(polyline)

    return AMapRoute(
        duration_minutes=max(1, round(duration_seconds / 60)),
        distance_meters=distance_meters,
        polyline=";".join(polylines),
    )


def driving_route(origin: tuple[float, float], destination: tuple[float, float]) -> AMapRoute | None:
    origin_latitude, origin_longitude = origin
    destination_latitude, destination_longitude = destination
    data = _amap_get(
        "/v3/direction/driving",
        {
            "origin": _format_location(origin_longitude, origin_latitude),
            "destination": _format_location(destination_longitude, destination_latitude),
            "extensions": "base",
            "strategy": "10",
            "output": "JSON",
        },
    )

    paths = data.get("route", {}).get("paths", [])
    if not paths:
        return None

    path = paths[0]
    duration_seconds = _to_int(path.get("duration"), default=0)
    distance_meters = _to_int(path.get("distance"), default=0)
    polylines = []
    for step in path.get("steps", []):
        polyline = step.get("polyline")
        if isinstance(polyline, str) and polyline:
            polylines.append(polyline)

    return AMapRoute(
        duration_minutes=max(1, round(duration_seconds / 60)),
        distance_meters=distance_meters,
        polyline=";".join(polylines),
        steps=(f"驾车约 {max(1, round(duration_seconds / 60))} 分钟，距离约 {round(distance_meters / 1000, 1)} 公里。",),
    )


def transit_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    city: str,
    destination_city: str | None = None,
) -> AMapRoute | None:
    origin_latitude, origin_longitude = origin
    destination_latitude, destination_longitude = destination
    data = _amap_get(
        "/v3/direction/transit/integrated",
        {
            "origin": _format_location(origin_longitude, origin_latitude),
            "destination": _format_location(destination_longitude, destination_latitude),
            "city": city,
            "cityd": destination_city or city,
            "strategy": "0",
            "nightflag": "0",
            "extensions": "base",
            "output": "JSON",
        },
    )

    transits = data.get("route", {}).get("transits", [])
    if not transits:
        return None

    transit = transits[0]
    duration_seconds = _to_int(transit.get("duration"), default=0)
    distance_meters = _to_int(transit.get("distance"), default=0)
    polylines: list[str] = []
    route_steps: list[str] = []

    for segment in transit.get("segments", []):
        if not isinstance(segment, dict):
            continue
        _append_walking_segment(segment.get("walking"), route_steps, polylines)
        _append_bus_segment(segment.get("bus"), route_steps, polylines)

    if not route_steps:
        route_steps.append(f"公交/地铁约 {max(1, round(duration_seconds / 60))} 分钟，距离约 {round(distance_meters / 1000, 1)} 公里。")

    return AMapRoute(
        duration_minutes=max(1, round(duration_seconds / 60)),
        distance_meters=distance_meters,
        polyline=";".join(polylines),
        steps=tuple(route_steps),
    )


def _append_walking_segment(raw_walking: Any, route_steps: list[str], polylines: list[str]) -> None:
    if not isinstance(raw_walking, dict):
        return

    distance_meters = _to_int(raw_walking.get("distance"), default=0)
    duration_minutes = max(1, round(_to_int(raw_walking.get("duration"), default=0) / 60))
    if distance_meters > 0:
        route_steps.append(f"步行约 {duration_minutes} 分钟，约 {distance_meters} 米。")

    for step in raw_walking.get("steps", []):
        if not isinstance(step, dict):
            continue
        instruction = step.get("instruction")
        road = step.get("road")
        if isinstance(instruction, str) and instruction:
            route_steps.append(instruction)
        elif isinstance(road, str) and road:
            route_steps.append(f"沿 {road} 步行。")
        polyline = step.get("polyline")
        if isinstance(polyline, str) and polyline:
            polylines.append(polyline)


def _append_bus_segment(raw_bus: Any, route_steps: list[str], polylines: list[str]) -> None:
    if not isinstance(raw_bus, dict):
        return

    for busline in raw_bus.get("buslines", []):
        if not isinstance(busline, dict):
            continue
        name = str(busline.get("name") or "公交/地铁线路")
        departure_stop = _stop_name(busline.get("departure_stop"))
        arrival_stop = _stop_name(busline.get("arrival_stop"))
        via_num = _to_int(busline.get("via_num"), default=0)
        duration_minutes = max(1, round(_to_int(busline.get("duration"), default=0) / 60))
        distance_meters = _to_int(busline.get("distance"), default=0)
        stop_text = f"，{via_num} 站" if via_num > 0 else ""
        distance_text = f"，约 {round(distance_meters / 1000, 1)} 公里" if distance_meters > 0 else ""
        route_steps.append(
            f"乘坐 {name}：{departure_stop} 上车，{arrival_stop} 下车{stop_text}，约 {duration_minutes} 分钟{distance_text}。"
        )
        polyline = busline.get("polyline")
        if isinstance(polyline, str) and polyline:
            polylines.append(polyline)


def _stop_name(raw_stop: Any) -> str:
    if isinstance(raw_stop, dict):
        name = raw_stop.get("name")
        if isinstance(name, str) and name:
            return name
    return "对应站点"


def _amap_get(path: str, params: dict[str, str]) -> dict[str, Any]:
    api_key = _get_env("MAP_API_KEY")
    if not api_key:
        raise RuntimeError("MAP_API_KEY is not configured.")

    query = urllib.parse.urlencode({**params, "key": api_key})
    url = f"{AMAP_REST_BASE_URL}{path}?{query}"
    request = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("AMap request failed.") from exc

    if data.get("status") != "1":
        message = data.get("info") or "AMap returned an error."
        raise RuntimeError(str(message))
    return data


def _parse_place(raw_poi: dict[str, Any]) -> AMapPlace | None:
    location = raw_poi.get("location")
    if not isinstance(location, str) or "," not in location:
        return None

    longitude_text, latitude_text = location.split(",", 1)
    try:
        longitude = float(longitude_text)
        latitude = float(latitude_text)
    except ValueError:
        return None

    address = raw_poi.get("address")
    if isinstance(address, list):
        address = ""

    return AMapPlace(
        name=str(raw_poi.get("name") or "Unnamed place"),
        type_name=str(raw_poi.get("type") or "POI"),
        address=str(address or ""),
        latitude=latitude,
        longitude=longitude,
    )


def _format_location(longitude: float, latitude: float) -> str:
    return f"{longitude:.6f},{latitude:.6f}"


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _get_env(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return None

    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            if key.strip() == name:
                return raw_value.strip().strip("\"'")
    return None
