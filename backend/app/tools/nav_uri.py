"""Build AMap navigation links (PROPOSAL §6.3).

Returns several flavours from one set of stops:
  * Android app deep link  ``amapuri://route/plan/`` (multi-waypoint)
  * iOS app deep link      ``iosamap://path``        (multi-waypoint, needs sourceApplication)
  * Web fallback           ``https://uri.amap.com/navigation`` (<=1 waypoint, desktop / no-app)
  * Per-segment Web links  one link per leg, so desktop users can still follow
    the complete itinerary when the one-shot Web URI cannot carry all vias.

Coordinates are GCJ-02. The app schemes take lat/lon as separate params;
the web link takes "lon,lat,name". The actual launch (iframe + ~2s timeout
fallback, WeChat handling) happens on the frontend — this only builds URIs.
"""

from __future__ import annotations

from urllib.parse import quote

from ..models.plan import NavLinks

# A waypoint/endpoint is (lng, lat, name).
Point = tuple[float, float, str]

SOURCE_APP = "RoamMind"


def _q(name: str) -> str:
    return quote(name, safe="")


def build_amap_nav_uri(
    origin: Point,
    destination: Point,
    waypoints: list[Point] | None = None,
) -> NavLinks:
    waypoints = waypoints or []
    s_lng, s_lat, s_name = origin
    d_lng, d_lat, d_name = destination

    # --- shared waypoint params for the app deep links -------------------
    via_params = ""
    if waypoints:
        vialons = "|".join(f"{w[0]:.6f}" for w in waypoints)
        vialats = "|".join(f"{w[1]:.6f}" for w in waypoints)
        vianames = "|".join(_q(w[2]) for w in waypoints)
        via_params = f"&vian={len(waypoints)}&vialons={vialons}&vialats={vialats}&vianames={vianames}"

    common = (
        f"slat={s_lat:.6f}&slon={s_lng:.6f}&sname={_q(s_name)}"
        f"&dlat={d_lat:.6f}&dlon={d_lng:.6f}&dname={_q(d_name)}"
        f"&t=0{via_params}"  # t=0 -> driving
    )
    app_uri_android = f"amapuri://route/plan/?{common}"
    app_uri_ios = f"iosamap://path?sourceApplication={SOURCE_APP}&dev=0&{common}"

    # --- web fallback (supports at most one via) -------------------------
    web = _build_web_uri(origin, destination, waypoints[:1])

    points = [origin, *waypoints, destination]
    segment_web_uris = [
        _build_web_uri(points[i], points[i + 1], [])
        for i in range(len(points) - 1)
    ]

    return NavLinks(
        app_uri_android=app_uri_android,
        app_uri_ios=app_uri_ios,
        web_uri=web,
        waypoint_count=len(waypoints),
        waypoint_names=[w[2] for w in waypoints],
        web_supports_all_waypoints=len(waypoints) <= 1,
        segment_web_uris=segment_web_uris,
    )


def _build_web_uri(origin: Point, destination: Point, waypoints: list[Point] | None = None) -> str:
    waypoints = waypoints or []
    s_lng, s_lat, s_name = origin
    d_lng, d_lat, d_name = destination
    web = (
        f"https://uri.amap.com/navigation"
        f"?from={s_lng:.6f},{s_lat:.6f},{_q(s_name)}"
        f"&to={d_lng:.6f},{d_lat:.6f},{_q(d_name)}"
    )
    if waypoints:
        w = waypoints[0]
        web += f"&via={w[0]:.6f},{w[1]:.6f},{_q(w[2])}"
    return web + "&mode=car&policy=1&src=" + SOURCE_APP + "&callnative=1"
