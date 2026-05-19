from app.schemas import CandidatePoiType, IntentParseResponse, PlannedTask, TaskPlanResponse, TaskStep


DEFAULT_POI_TYPES = [
    CandidatePoiType(
        type="general_poi",
        keywords=["nearby place", "point of interest"],
        priority=1,
        reason="Fallback type for unclear tasks.",
    )
]

POI_TYPE_RULES: dict[str, list[CandidatePoiType]] = {
    "food": [
        CandidatePoiType(type="restaurant", keywords=["restaurant", "meal"], priority=1, reason="Best match for eating tasks."),
        CandidatePoiType(type="snack", keywords=["snack", "street food"], priority=2, reason="Useful for quick food stops."),
        CandidatePoiType(type="canteen", keywords=["canteen", "cafeteria"], priority=3, reason="Useful in campus scenarios."),
    ],
    "sports": [
        CandidatePoiType(type="sports_court", keywords=["court", "basketball", "badminton"], priority=1, reason="Direct match for ball games."),
        CandidatePoiType(type="gym", keywords=["gym", "fitness center"], priority=2, reason="Indoor alternative for sports tasks."),
        CandidatePoiType(type="campus_playground", keywords=["playground", "sports field"], priority=3, reason="Common low-cost option on campus."),
    ],
    "cafe": [
        CandidatePoiType(type="coffee_shop", keywords=["coffee", "cafe"], priority=1, reason="Direct match for coffee tasks."),
        CandidatePoiType(type="bakery", keywords=["bakery", "dessert"], priority=2, reason="Often works for coffee and light food."),
    ],
    "study": [
        CandidatePoiType(type="library", keywords=["library", "reading room"], priority=1, reason="Direct match for study or library tasks."),
        CandidatePoiType(type="study_room", keywords=["study room", "quiet space"], priority=2, reason="Good backup when libraries are unavailable."),
        CandidatePoiType(type="bookstore", keywords=["bookstore", "books"], priority=3, reason="Can serve as a quiet reading destination."),
    ],
    "errand": [
        CandidatePoiType(type="package_station", keywords=["package", "parcel", "pickup"], priority=1, reason="Direct match for package pickup."),
        CandidatePoiType(type="delivery_locker", keywords=["locker", "smart locker"], priority=2, reason="Common parcel pickup facility."),
        CandidatePoiType(type="service_counter", keywords=["service counter", "front desk"], priority=3, reason="Backup for manually handled pickup."),
    ],
    "return": [
        CandidatePoiType(type="user_destination", keywords=["dorm", "home", "destination"], priority=1, reason="Return tasks need a fixed destination."),
        CandidatePoiType(type="transit_stop", keywords=["bus stop", "metro station"], priority=2, reason="May help route planning for the return leg."),
    ],
}

ACTION_POI_RULES: list[tuple[list[str], list[CandidatePoiType]]] = [
    (["eat", "meal", "food"], POI_TYPE_RULES["food"]),
    (["play ball", "basketball", "badminton", "sport"], POI_TYPE_RULES["sports"]),
    (["coffee", "cafe"], POI_TYPE_RULES["cafe"]),
    (["library", "study", "read"], POI_TYPE_RULES["study"]),
    (["package", "parcel", "pickup"], POI_TYPE_RULES["errand"]),
    (["return", "home", "dorm"], POI_TYPE_RULES["return"]),
]


def build_task_plan(intent: IntentParseResponse) -> TaskPlanResponse:
    planned_tasks = [_plan_task(task) for task in sorted(intent.tasks, key=lambda item: item.order)]
    return TaskPlanResponse(
        original_query=intent.original_query,
        intent_source=intent.source,
        tasks=planned_tasks,
        assumptions=intent.assumptions,
    )


def _plan_task(task: TaskStep) -> PlannedTask:
    candidate_types = _candidate_types_for_task(task)
    return PlannedTask(
        order=task.order,
        action=task.action,
        category=task.category,
        candidate_poi_types=candidate_types,
        required_resource=task.required_resource or "poi_search",
        query_hint=task.query_hint,
    )


def _candidate_types_for_task(task: TaskStep) -> list[CandidatePoiType]:
    category_key = task.category.strip().lower()
    if category_key in POI_TYPE_RULES:
        return POI_TYPE_RULES[category_key]

    action_text = f"{task.action} {task.query_hint}".lower()
    for keywords, candidate_types in ACTION_POI_RULES:
        if any(keyword in action_text for keyword in keywords):
            return candidate_types

    return DEFAULT_POI_TYPES
