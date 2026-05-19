from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import IntentParseResponse, RankPlansRequest, RankPlansResponse, TaskPlanResponse, TravelPlanRequest, TravelPlanResponse
from app.services.intent_parser import parse_user_intent
from app.services.amap_planner import build_amap_plan
from app.services.mock_planner import build_mock_plan
from app.services.plan_ranker import rank_candidate_plans
from app.services.task_planner import build_task_plan

app = FastAPI(
    title="Travel Planning Assistant API",
    description="Mock backend for a task-oriented intelligent travel planning assistant.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/plans", response_model=TravelPlanResponse)
def create_travel_plan(payload: TravelPlanRequest) -> TravelPlanResponse:
    try:
        amap_plan = build_amap_plan(payload)
        if amap_plan is not None:
            return amap_plan
    except RuntimeError:
        pass
    return build_mock_plan(payload)


@app.post("/api/intent", response_model=IntentParseResponse)
def parse_intent(payload: TravelPlanRequest) -> IntentParseResponse:
    return parse_user_intent(payload.query)


@app.post("/api/task-plan", response_model=TaskPlanResponse)
def create_task_plan(payload: TravelPlanRequest) -> TaskPlanResponse:
    intent = parse_user_intent(payload.query)
    return build_task_plan(intent)


@app.post("/api/rank-plans", response_model=RankPlansResponse)
def rank_plans(payload: RankPlansRequest) -> RankPlansResponse:
    return rank_candidate_plans(payload.plans, payload.user_preferences)
