"""API contracts: chat request, the structured Plan the frontend renders,
and the typed stream events.

The Plan shape maps 1:1 onto the mockup (understanding card -> timeline ->
map summary -> nav button), so the frontend can render it without reshaping.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .intent import IntentObject

Role = Literal["user", "assistant"]


# --------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: Role
    content: str


class GeoPoint(BaseModel):
    lng: float
    lat: float
    label: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    city: Optional[str] = None
    # Browser geolocation for the trip start (§7.1). Optional.
    origin: Optional[GeoPoint] = None
    # Carry the Intent Object across turns for incremental replanning (§6.4.6).
    intent: Optional[IntentObject] = None
    # Force a built-in demo scenario ("sc1".."sc4"); used by the preset chips.
    scenario: Optional[str] = None


# --------------------------------------------------------------------------
# Understanding card ("我想你需要" / "我听明白了")
# --------------------------------------------------------------------------
class UnderstandStep(BaseModel):
    index: str  # "1" | "终" | "🔒"
    label: str
    locked: bool = False


class Understanding(BaseModel):
    kind: Literal["mood", "explicit"]
    title: str
    # mood variant
    mood_chips: list[str] = Field(default_factory=list)
    want_chips: list[str] = Field(default_factory=list)
    avoid_chips: list[str] = Field(default_factory=list)
    # explicit variant
    steps: list[UnderstandStep] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Timeline + route
# --------------------------------------------------------------------------
class POIChoice(BaseModel):
    """A real candidate the user can swap a stop to (the 备选 dropdown)."""

    name: str
    location: list[float]  # [lng, lat], GCJ-02
    rating: Optional[float] = None
    cost: Optional[str] = None
    address: Optional[str] = None


class Stop(BaseModel):
    kind: Literal["start", "poi", "end", "fixed"] = "poi"
    marker: str  # "📍" | "1" | "🏠" | "🔒"
    time: str  # "现在" | "11:30" | "10:00–12:00"
    name: str
    tags: list[str] = Field(default_factory=list)
    rating: Optional[float] = None
    cost: Optional[str] = None  # "¥130/人"
    why: Optional[str] = None
    leg: Optional[str] = None  # "🚗 驾车 12 min · 3.5 km · 停留约 90 min"
    location: Optional[list[float]] = None  # [lng, lat], GCJ-02
    open_info: Optional[str] = None
    dwell_min: int = 0  # carried so a swap can recompute the timeline deterministically
    # Real alternatives for this stop (user picks -> deterministic re-route, no LLM).
    alternatives: list[POIChoice] = Field(default_factory=list)


class RouteSegment(BaseModel):
    from_index: int
    to_index: int
    from_name: str
    to_name: str
    mode: Literal["walking", "driving", "transit", "auto"] = "auto"
    polyline: list[list[float]] = Field(default_factory=list)


class RouteSummary(BaseModel):
    total_distance_text: str = ""
    total_duration_text: str = ""
    stop_count: int = 0
    # Decoded polyline points [[lng, lat], ...] for the real AMap line.
    # Empty -> the frontend draws its stylized SVG fallback route.
    polyline: list[list[float]] = Field(default_factory=list)
    # Per-leg geometry for colored route rendering. Kept separate from the
    # flattened `polyline` so older clients can keep drawing a single route.
    segments: list[RouteSegment] = Field(default_factory=list)


class NavLinks(BaseModel):
    app_uri_android: str = ""
    app_uri_ios: str = ""
    web_uri: str = ""
    waypoint_count: int = 0
    waypoint_names: list[str] = Field(default_factory=list)
    web_supports_all_waypoints: bool = True
    segment_web_uris: list[str] = Field(default_factory=list)


class ClarifyOption(BaseModel):
    id: str
    label: str
    description: str = ""
    # Full message sent when the user clicks this option. It intentionally
    # carries the original request so the heuristic fallback can re-extract it.
    message: str


class Feasibility(BaseModel):
    ok: bool = True
    note: str = ""


class Plan(BaseModel):
    city: str
    panel_hint: str = ""  # "放松向 · 4 站"
    understanding: Optional[Understanding] = None
    summary: RouteSummary = Field(default_factory=RouteSummary)
    timeline: list[Stop] = Field(default_factory=list)
    nav: NavLinks = Field(default_factory=NavLinks)
    feasibility: Feasibility = Field(default_factory=Feasibility)
    # Echoed back so the client can send it on the next (replanning) turn.
    intent: Optional[IntentObject] = None
    source: Literal["mock", "live"] = "mock"


class RouteSwapRequest(BaseModel):
    """Swap one stop to a chosen alternative and recompute the route. The client
    has already replaced the stop's name/location in ``timeline``; the server
    just re-runs legs/times/nav deterministically (no LLM, no re-grounding)."""

    timeline: list[Stop]
    city: str
    intent: Optional[IntentObject] = None


# --------------------------------------------------------------------------
# Stream events (SSE-style, one JSON object per `data:` line)
# --------------------------------------------------------------------------
class StreamEvent(BaseModel):
    type: Literal[
        "thinking",       # progress line: "正在帮你找安静的去处…"
        "understanding",  # the understanding card is ready
        "clarify",        # ask one clarifying question, no plan yet
        "message",        # streamed narration token(s)
        "plan",           # the full structured Plan
        "done",
        "error",
    ]
    text: Optional[str] = None
    understanding: Optional[Understanding] = None
    plan: Optional[Plan] = None
    options: list[ClarifyOption] = Field(default_factory=list)
    intent: Optional[IntentObject] = None
