from app.schemas import CandidateRoutePlan, RankedPlan, RankPlansResponse, ScoreBreakdown

DURATION_WEIGHT = 0.30
DETOUR_WEIGHT = 0.25
POI_TYPE_WEIGHT = 0.25
PREFERENCE_WEIGHT = 0.20


def rank_candidate_plans(
    plans: list[CandidateRoutePlan],
    user_preferences: list[str] | None = None,
) -> RankPlansResponse:
    preferences = [preference.strip().lower() for preference in (user_preferences or []) if preference.strip()]
    if not plans:
        return RankPlansResponse(ranked_plans=[])

    min_duration = min(plan.total_duration_minutes for plan in plans)
    max_duration = max(plan.total_duration_minutes for plan in plans)
    min_detour = min(plan.detour_distance_km for plan in plans)
    max_detour = max(plan.detour_distance_km for plan in plans)

    scored = [
        _score_plan(
            plan=plan,
            preferences=preferences,
            min_duration=min_duration,
            max_duration=max_duration,
            min_detour=min_detour,
            max_detour=max_detour,
        )
        for plan in plans
    ]
    scored.sort(key=lambda plan: (-plan.score, plan.total_duration_minutes, plan.detour_distance_km))

    ranked_plans = []
    for index, plan in enumerate(scored, start=1):
        ranked_plans.append(plan.model_copy(update={"rank": index}))

    return RankPlansResponse(ranked_plans=ranked_plans)


def _score_plan(
    plan: CandidateRoutePlan,
    preferences: list[str],
    min_duration: int,
    max_duration: int,
    min_detour: float,
    max_detour: float,
) -> RankedPlan:
    duration_score = _inverse_score(plan.total_duration_minutes, min_duration, max_duration)
    detour_score = _inverse_score(plan.detour_distance_km, min_detour, max_detour)
    poi_type_score = _clamp(plan.poi_type_match)
    preference_score = _preference_score(plan, preferences)

    score = (
        duration_score * DURATION_WEIGHT
        + detour_score * DETOUR_WEIGHT
        + poi_type_score * POI_TYPE_WEIGHT
        + preference_score * PREFERENCE_WEIGHT
    )
    breakdown = ScoreBreakdown(
        duration_score=round(duration_score, 4),
        detour_score=round(detour_score, 4),
        poi_type_score=round(poi_type_score, 4),
        preference_score=round(preference_score, 4),
    )

    return RankedPlan(
        **plan.model_dump(),
        score=round(score, 4),
        rank=0,
        score_breakdown=breakdown,
        ranking_reason=_build_reason(breakdown),
    )


def _inverse_score(value: float, min_value: float, max_value: float) -> float:
    if max_value <= min_value:
        return 1.0
    return _clamp(1 - ((value - min_value) / (max_value - min_value)))


def _preference_score(plan: CandidateRoutePlan, preferences: list[str]) -> float:
    if not preferences:
        return 1.0

    searchable = " ".join(plan.matched_preferences + plan.poi_types + [plan.title]).lower()
    matches = sum(1 for preference in preferences if preference in searchable)
    return _clamp(matches / len(preferences))


def _build_reason(breakdown: ScoreBreakdown) -> str:
    best_factor = max(
        [
            ("shorter total time", breakdown.duration_score),
            ("less detour distance", breakdown.detour_score),
            ("better POI type match", breakdown.poi_type_score),
            ("stronger preference match", breakdown.preference_score),
        ],
        key=lambda item: item[1],
    )
    return f"Ranked highly because of {best_factor[0]}."


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
