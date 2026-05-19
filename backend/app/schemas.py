from pydantic import BaseModel, Field


class DeviceLocation(BaseModel):
    latitude: float
    longitude: float
    accuracy_meters: float | None = None
    label: str | None = None


class TravelPlanRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=2,
        examples=["Plan a relaxed one-day Hangzhou trip with nature views"],
    )
    current_location: DeviceLocation | None = None


class Intent(BaseModel):
    destination: str
    duration: str
    preferences: list[str]


class Poi(BaseModel):
    name: str
    category: str
    address: str
    stay_minutes: int
    reason: str
    latitude: float | None = None
    longitude: float | None = None
    source: str = "mock"


class RouteLeg(BaseModel):
    from_place: str = Field(alias="from")
    to: str
    transport: str
    duration_minutes: int
    distance_meters: int | None = None
    polyline: str | None = None
    steps: list[str] = []

    model_config = {"populate_by_name": True}


class TravelPlan(BaseModel):
    title: str
    score: float
    summary: str
    pois: list[Poi]
    route: list[RouteLeg]
    explanation: str


class TravelPlanResponse(BaseModel):
    intent: Intent
    plans: list[TravelPlan]


class TaskStep(BaseModel):
    order: int
    action: str
    category: str
    query_hint: str
    required_resource: str | None = None
    notes: str | None = None


class IntentParseResponse(BaseModel):
    original_query: str
    source: str
    tasks: list[TaskStep]
    task_chain: list[str]
    assumptions: list[str] = []


class CandidatePoiType(BaseModel):
    type: str
    keywords: list[str]
    priority: int
    reason: str


class PlannedTask(BaseModel):
    order: int
    action: str
    category: str
    candidate_poi_types: list[CandidatePoiType]
    required_resource: str
    query_hint: str


class TaskPlanResponse(BaseModel):
    original_query: str
    intent_source: str
    tasks: list[PlannedTask]
    assumptions: list[str] = []


class CandidateRoutePlan(BaseModel):
    id: str
    title: str
    total_duration_minutes: int
    detour_distance_km: float
    poi_type_match: float = Field(ge=0, le=1)
    matched_preferences: list[str] = []
    poi_types: list[str] = []


class RankPlansRequest(BaseModel):
    user_preferences: list[str] = []
    plans: list[CandidateRoutePlan]


class ScoreBreakdown(BaseModel):
    duration_score: float
    detour_score: float
    poi_type_score: float
    preference_score: float


class RankedPlan(CandidateRoutePlan):
    score: float
    rank: int
    score_breakdown: ScoreBreakdown
    ranking_reason: str


class RankPlansResponse(BaseModel):
    ranked_plans: list[RankedPlan]
