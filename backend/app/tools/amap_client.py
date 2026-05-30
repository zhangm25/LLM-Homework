"""AMap Web Service client (server side).

Search POI 2.0 (``/v5/place/*`` with ``show_fields=business,photos``) for
grounding, and Route Planning 2.0 (``/v5/direction/*``) for legs/polylines.
All coordinates are GCJ-02 "lng,lat". Every method degrades to ``None``/``[]``
on failure so the planner can fall back instead of inventing data (NFR-2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

import httpx

from ..config import get_settings
from ..debug_log import write_debug_log

AMAP_BASE_URL = "https://restapi.amap.com"
# infocodes meaning "slow down" (QPS / concurrency / daily ceiling) — retry once.
_RETRY_INFOCODES = {"10004", "10014", "10019", "10020", "10021", "10022", "10029"}


@dataclass
class POI:
    id: str
    name: str
    address: str
    location: list[float]  # [lng, lat]
    type: str = ""
    distance_m: Optional[int] = None
    rating: Optional[float] = None
    cost: Optional[str] = None  # per-person, as returned (text)
    opentime_today: Optional[str] = None
    tel: Optional[str] = None
    tag: Optional[str] = None
    photos: list[str] = field(default_factory=list)


@dataclass
class Leg:
    mode: str  # walking | driving | transit
    distance_m: int
    duration_s: int
    cost: Optional[str] = None  # fare text where available
    polyline: list[list[float]] = field(default_factory=list)  # [[lng,lat],...]
    transfers: Optional[str] = None  # transit transfer summary


def _fmt(point: list[float] | tuple[float, float]) -> str:
    return f"{point[0]:.6f},{point[1]:.6f}"


def _parse_point(text: str) -> Optional[list[float]]:
    if not isinstance(text, str) or "," not in text:
        return None
    lng_s, lat_s = text.split(",", 1)
    try:
        return [float(lng_s), float(lat_s)]
    except ValueError:
        return None


def _parse_polyline(text: str) -> list[list[float]]:
    points: list[list[float]] = []
    for pair in (text or "").split(";"):
        pt = _parse_point(pair)
        if pt is not None:
            points.append(pt)
    return points


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AMapClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._key = settings.amap_web_service_key
        self._timeout = settings.request_timeout_s
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=AMAP_BASE_URL, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any], _retries: int = 1) -> Optional[dict]:
        if not self.enabled:
            return None
        query = {k: v for k, v in params.items() if v is not None}
        query["key"] = self._key
        try:
            resp = await self._http().get(path, params=query)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            if _retries > 0:
                await asyncio.sleep(0.4)
                return await self._get(path, params, _retries - 1)
            return None
        # v5 success: status == "1" (infocode "10000")
        if str(data.get("status")) != "1":
            if _retries > 0 and str(data.get("infocode")) in _RETRY_INFOCODES:
                await asyncio.sleep(0.4)
                return await self._get(path, params, _retries - 1)
            return None
        return data

    # --- POI search (grounding) ------------------------------------------
    async def search_poi_text(
        self,
        keywords: str,
        region: Optional[str] = None,
        types: Optional[str] = None,
        page_size: int = 10,
    ) -> list[POI]:
        data = await self._get(
            "/v5/place/text",
            {
                "keywords": keywords,
                "region": region,
                "city_limit": "true" if region else None,
                "types": types,
                "show_fields": "business,photos",
                "page_size": page_size,
            },
        )
        pois = _parse_pois(data)
        _log_poi_search(
            endpoint="/v5/place/text",
            params={
                "keywords": keywords,
                "region": region,
                "city_limit": bool(region),
                "types": types,
                "page_size": page_size,
            },
            pois=pois,
        )
        return pois

    async def search_poi_around(
        self,
        keywords: str,
        location: list[float],
        radius_m: int = 3000,
        types: Optional[str] = None,
        sortrule: str = "weight",  # weight | distance
        page_size: int = 10,
    ) -> list[POI]:
        data = await self._get(
            "/v5/place/around",
            {
                "keywords": keywords,
                "location": _fmt(location),
                "radius": radius_m,
                "types": types,
                "sortrule": sortrule,
                "show_fields": "business,photos",
                "page_size": page_size,
            },
        )
        pois = _parse_pois(data)
        _log_poi_search(
            endpoint="/v5/place/around",
            params={
                "keywords": keywords,
                "location": _fmt(location),
                "radius_m": radius_m,
                "types": types,
                "sortrule": sortrule,
                "page_size": page_size,
            },
            pois=pois,
        )
        return pois

    # --- Routing ---------------------------------------------------------
    async def route_driving(
        self,
        origin: list[float],
        destination: list[float],
        waypoints: Optional[list[list[float]]] = None,
    ) -> Optional[Leg]:
        data = await self._get(
            "/v5/direction/driving",
            {
                "origin": _fmt(origin),
                "destination": _fmt(destination),
                "waypoints": ";".join(_fmt(w) for w in waypoints) if waypoints else None,
                "show_fields": "cost,polyline",
            },
        )
        return _parse_path_leg(data, "driving")

    async def route_walking(
        self, origin: list[float], destination: list[float]
    ) -> Optional[Leg]:
        data = await self._get(
            "/v5/direction/walking",
            {
                "origin": _fmt(origin),
                "destination": _fmt(destination),
                "show_fields": "cost,polyline",
            },
        )
        return _parse_path_leg(data, "walking")

    async def route_transit(
        self,
        origin: list[float],
        destination: list[float],
        city1: str,
        city2: Optional[str] = None,
    ) -> Optional[Leg]:
        data = await self._get(
            "/v5/direction/transit/integrated",
            {
                "origin": _fmt(origin),
                "destination": _fmt(destination),
                "city1": city1,
                "city2": city2 or city1,
                "show_fields": "cost,polyline",
            },
        )
        return _parse_transit_leg(data)

    async def reverse_geocode(self, location: list[float]) -> Optional[str]:
        data = await self._get(
            "/v3/geocode/regeo",
            {
                "location": _fmt(location),
                "extensions": "base",
                "radius": 1000,
            },
        )
        if not data:
            return None
        regeocode = data.get("regeocode") or {}
        formatted = regeocode.get("formatted_address")
        return str(formatted) if formatted else None

    async def geocode_place(self, query: str, city: Optional[str] = None) -> Optional[POI]:
        pois = await self.search_poi_text(query, region=city, page_size=1)
        return pois[0] if pois else None


def _parse_pois(data: Optional[dict]) -> list[POI]:
    if not data:
        return []
    out: list[POI] = []
    for raw in data.get("pois", []):
        loc = _parse_point(raw.get("location", ""))
        if loc is None:
            continue
        business = raw.get("business") or {}
        photos = [p.get("url") for p in (raw.get("photos") or []) if p.get("url")]
        out.append(
            POI(
                id=str(raw.get("id") or raw.get("name") or ""),
                name=str(raw.get("name") or "未命名地点"),
                address=str(raw.get("address") or ""),
                location=loc,
                type=str(raw.get("type") or ""),
                distance_m=_to_int(raw.get("distance")) if raw.get("distance") else None,
                rating=_to_float(business.get("rating")),
                cost=str(business.get("cost")) if business.get("cost") else None,
                opentime_today=business.get("opentime_today") or None,
                tel=business.get("tel") or None,
                tag=business.get("tag") or None,
                photos=[str(p) for p in photos],
            )
        )
    return out


def _log_poi_search(endpoint: str, params: dict[str, Any], pois: list[POI]) -> None:
    top_results = [
        {
            "name": poi.name,
            "address": poi.address,
            "location": _fmt(poi.location),
            "type": poi.type,
            "distance_m": poi.distance_m,
            "rating": poi.rating,
        }
        for poi in pois[:5]
    ]
    write_debug_log(
        "AMAP POI SEARCH\n"
        f"endpoint: {endpoint}\n"
        f"params: {params}\n"
        f"result_count: {len(pois)}\n"
        f"top_results: {top_results}"
    )


def _parse_path_leg(data: Optional[dict], mode: str) -> Optional[Leg]:
    if not data:
        return None
    paths = (data.get("route") or {}).get("paths") or []
    if not paths:
        return None
    path = paths[0]
    cost = path.get("cost") or {}
    polyline: list[list[float]] = []
    for step in path.get("steps", []):
        polyline.extend(_parse_polyline(step.get("polyline", "")))
    return Leg(
        mode=mode,
        distance_m=_to_int(path.get("distance")),
        duration_s=_to_int(cost.get("duration")),
        cost=(f"¥{cost.get('tolls')}" if _to_int(cost.get("tolls")) > 0 else None),
        polyline=polyline,
    )


def _parse_transit_leg(data: Optional[dict]) -> Optional[Leg]:
    if not data:
        return None
    route = data.get("route") or {}
    transits = route.get("transits") or []
    if not transits:
        return None
    transit = transits[0]
    cost = transit.get("cost") or {}
    polyline: list[list[float]] = []
    transfer_names: list[str] = []
    for seg in transit.get("segments", []):
        bus = (seg.get("bus") or {}).get("buslines") or []
        for line in bus:
            name = line.get("name")
            if name:
                transfer_names.append(str(name))
            polyline.extend(_parse_polyline(line.get("polyline", {}).get("polyline", "") if isinstance(line.get("polyline"), dict) else line.get("polyline", "")))
    fare = cost.get("transit_fee")
    return Leg(
        mode="transit",
        distance_m=_to_int(transit.get("distance")),
        duration_s=_to_int(cost.get("duration")),
        cost=(f"¥{fare}" if fare else None),
        polyline=polyline,
        transfers=" → ".join(transfer_names) if transfer_names else None,
    )


@lru_cache
def get_amap() -> AMapClient:
    return AMapClient()
