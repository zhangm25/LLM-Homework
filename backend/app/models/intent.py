"""The Intent Object — structured output of P1 (intent extraction).

This mirrors PROPOSAL §6.2. It is the shared context that every later stage
(grounding, scheduling, narration, replanning) reads and patches. Every
inferred field carries ``explicit`` provenance so the UI can show "what I
understood" honestly and never fabricate a hard constraint (§6.4.3).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _strip_none(obj: Any) -> Any:
    """Recursively drop keys whose value is null. LLMs (esp. smaller ones) love
    emitting explicit `null` for fields they have nothing to say about; dropping
    them lets pydantic apply the declared defaults instead of failing validation
    (which would silently fall back to the weaker heuristic extractor)."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj

TransportMode = Literal["auto", "walking", "driving", "transit"]
TaskType = Literal[
    "dining",
    "leisure",
    "sightseeing",
    "shopping",
    "sports",
    "pickup",
    "meeting",
    "commute",
    "other",
]


class ExplicitPOI(BaseModel):
    """A place the user named outright (clear-intent input)."""

    name: str
    category: Optional[str] = None
    fixed_order_index: Optional[int] = None


class ImplicitPreferences(BaseModel):
    """Inferred from mood / fuzzy phrasing (the emotional-input showcase)."""

    mood: Optional[str] = None
    vibe_tags: list[str] = Field(default_factory=list)
    avoid_tags: list[str] = Field(default_factory=list)
    desired_categories: list[str] = Field(default_factory=list)


class Endpoint(BaseModel):
    """Trip start/end. Start defaults to the user's current location."""

    type: Literal["current", "named"] = "current"
    value: Optional[str] = None
    source: Literal["geolocation", "nl_extract", "default"] = "default"
    location: Optional[list[float]] = None  # [lng, lat], GCJ-02


class TimeWindow(BaseModel):
    start: Optional[str] = None  # "now" | "HH:MM"
    end: Optional[str] = None


class Constraints(BaseModel):
    city: Optional[str] = None  # not hard-coded; inferred from geo/utterance
    start: Endpoint = Field(default_factory=Endpoint)
    end: Optional[Endpoint] = None
    time_window: TimeWindow = Field(default_factory=TimeWindow)
    transport: TransportMode = "auto"
    budget_total: Optional[float] = None
    area_scope: Optional[str] = None


class Task(BaseModel):
    """A unit of the day. ``needs_poi=False`` tasks (pickup/commute) are pure
    anchors that go straight into scheduling without a POI search."""

    id: str
    type: TaskType = "other"
    intent: str = ""
    dwell_min: int = 60
    needs_poi: bool = True
    time_hint: Optional[str] = None  # e.g. ">=11:00", "~黄昏"
    at: Optional[str] = None  # named place for non-POI tasks
    explicit: bool = False
    confidence: Optional[float] = None

    @field_validator("dwell_min", mode="before")
    @classmethod
    def _clamp_dwell(cls, v: Any) -> int:
        """A stop can't sensibly last 0 or 100000 minutes. Clamp at the model
        boundary so a bad LLM number or a hand-crafted replanning payload can't
        blow up the timeline (defense-in-depth; the pipeline also sanitises)."""
        try:
            return max(0, min(int(v), 360))
        except (TypeError, ValueError):
            return 60


class FixedEvent(BaseModel):
    """A hard time+place anchor (meeting / appointment / agenda item)."""

    title: str
    place: str
    start: str  # "HH:MM"
    end: Optional[str] = None
    location: Optional[list[float]] = None


class IntentObject(BaseModel):
    explicit_pois: list[ExplicitPOI] = Field(default_factory=list)
    implicit_preferences: ImplicitPreferences = Field(default_factory=ImplicitPreferences)
    date: str = "today"
    constraints: Constraints = Field(default_factory=Constraints)
    tasks: list[Task] = Field(default_factory=list)
    fixed_events: list[FixedEvent] = Field(default_factory=list)
    clarification_needed: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        return _strip_none(data) if isinstance(data, dict) else data

    @property
    def is_explicit(self) -> bool:
        """Clear-intent input drives the "我听明白了" card; otherwise the
        emotional "我想你需要" card. A fixed-schedule day (meetings) is always
        explicit — it must show the schedule card, not the mood card."""
        return bool(self.explicit_pois) or bool(self.fixed_events) or any(t.explicit for t in self.tasks)
