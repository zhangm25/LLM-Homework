"""The orchestration pipeline (PROPOSAL §3 flow, §6.4.1 prompt chain).

`plan_stream` is an async generator of typed StreamEvents:
    thinking -> understanding -> plan -> message(stream) -> done

Preset demo scripts short-circuit to the polished canned plan (NFR-5, stable
live demo). Free text runs the deterministic chain: P1 intent extraction
(LLM or heuristic) -> grounding+scheduling -> P5 narration (LLM or templated).
Every external call degrades gracefully, so the stream never dead-ends.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator, Optional

from ..config import get_settings
from ..llm.client import get_llm
from ..llm.prompts import (
    P1_INTENT_SYSTEM,
    P1_INTENT_USER,
    P1_PATCH_SYSTEM,
    P1_PATCH_USER,
    P1_REPAIR_SYSTEM,
    P1_REPAIR_USER,
    P1_SEGMENTS_SYSTEM,
    P1_SEGMENTS_USER,
    P5_NARRATE_SYSTEM,
    P5_NARRATE_USER,
)
from ..models.intent import (
    Constraints,
    Endpoint,
    ExplicitPOI,
    FixedEvent,
    ImplicitPreferences,
    IntentObject,
    Task,
    TimeWindow,
)
from ..models.plan import ChatRequest, ClarifyOption, Plan, StreamEvent, Understanding
from ..mock.demo_data import get_demo
from ..planner.scheduler import build_plan, build_understanding
from .heuristics import augment_intent, extract_intent_heuristic
from .place_norm import normalize_place_name

STREAM_DELAY = 0.02  # pacing for non-LLM narration, gives a "typing" feel
NARRATION_CHUNK = 2  # characters per emitted chunk in fallback mode


def _ev(**kwargs) -> StreamEvent:
    return StreamEvent(**kwargs)


async def _emit_paced(text: str) -> AsyncIterator[StreamEvent]:
    for i in range(0, len(text), NARRATION_CHUNK):
        yield _ev(type="message", text=text[i : i + NARRATION_CHUNK])
        await asyncio.sleep(STREAM_DELAY)


def _format_history(req: ChatRequest, limit: int = 6) -> str:
    rows = req.history[-limit:]
    if not rows:
        return "（无）"
    name = {"user": "用户", "assistant": "RoamMind"}
    return "\n".join(f"{name[m.role]}: {m.content}" for m in rows)


def _itinerary_text(plan: Plan) -> str:
    lines = []
    for s in plan.timeline:
        bits = [s.time, s.name]
        if s.leg:
            bits.append(f"({s.leg})")
        lines.append(" ".join(b for b in bits if b))
    return "\n".join(lines)


def _intent_summary(intent: IntentObject) -> str:
    prefs = intent.implicit_preferences
    parts = []
    if prefs.mood:
        parts.append(f"心情：{prefs.mood}")
    if prefs.vibe_tags:
        parts.append("想要：" + "、".join(prefs.vibe_tags))
    if intent.tasks:
        parts.append("任务：" + "、".join(t.intent or t.type for t in intent.tasks))
    return " · ".join(parts) or "（休闲出行）"


def _extraction_detail(intent: IntentObject) -> str:
    places = [p.name for p in sorted(
        intent.explicit_pois,
        key=lambda p: p.fixed_order_index if p.fixed_order_index is not None else 999,
    )]
    tasks = [t.intent or t.type for t in intent.tasks]
    bits = []
    if places:
        bits.append("地点：" + " → ".join(places[:5]))
    if tasks:
        bits.append("事项：" + " → ".join(tasks[:5]))
    if intent.constraints.end and intent.constraints.end.value:
        bits.append("终点：" + intent.constraints.end.value)
    return "已提取 " + "；".join(bits) if bits else "已提取你的偏好，正在细化路线。"


def _fallback_narration(plan: Plan) -> str:
    pois = [s for s in plan.timeline if s.kind == "poi"]
    names = "、".join(s.name for s in pois[:3])
    tail = f"，最后{plan.timeline[-1].name}" if plan.timeline and plan.timeline[-1].kind == "end" else ""
    return f"给你排好了：{names}{tail}。时间和路程都核对过，{plan.feasibility.note}"


def _as_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _task_dwell(task_type: str, task: str, value) -> int:
    if isinstance(value, int) and 0 <= value <= 360:
        return value
    if task_type == "dining":
        return 60
    if task_type == "pickup":
        return 10
    if task_type == "meeting":
        return 120
    if "电影" in task:
        return 120
    if task_type in {"sightseeing", "leisure"}:
        return 70
    return 60


def _transport(value) -> str:
    text = _as_str(value) or "auto"
    if "走" in text or "步行" in text:
        return "walking"
    if text in {"auto", "walking", "driving", "transit"}:
        return text
    return "auto"


def _intent_from_segments(data: dict) -> IntentObject:
    start_data = data.get("start") or {}
    end_data = data.get("end") or {}
    start_place = normalize_place_name(_as_str(start_data.get("place")))
    end_place = normalize_place_name(_as_str(end_data.get("place")))

    explicit_pois: list[ExplicitPOI] = []
    tasks: list[Task] = []
    for i, seg in enumerate(data.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        place = normalize_place_name(_as_str(seg.get("place")))
        task = _as_str(seg.get("task")) or ""
        task_type = _as_str(seg.get("task_type")) or "other"
        if task_type not in {
            "dining", "leisure", "sightseeing", "shopping", "sports",
            "pickup", "meeting", "commute", "other",
        }:
            task_type = "other"
        place_for_task = place if place and place != end_place else None
        if place_for_task:
            explicit_pois.append(ExplicitPOI(name=place, fixed_order_index=len(explicit_pois)))
        tasks.append(Task(
            id=f"t{i + 1}",
            type=task_type,
            intent=task,
            dwell_min=_task_dwell(task_type, task, seg.get("dwell_min")),
            needs_poi=True,
            at=place_for_task,
            time_hint=_as_str(seg.get("time_hint")),
            explicit=bool(place or task),
        ))

    fixed_events = []
    for raw in data.get("fixed_events") or []:
        if not isinstance(raw, dict):
            continue
        title = _as_str(raw.get("title")) or "固定日程"
        place = _as_str(raw.get("place"))
        start = _as_str(raw.get("start"))
        if place and start:
            fixed_events.append(FixedEvent(
                title=title,
                place=normalize_place_name(place) or place,
                start=start,
                end=_as_str(raw.get("end")),
            ))

    prefs = ImplicitPreferences(
        mood=_as_str(data.get("mood")),
        vibe_tags=[str(x) for x in (data.get("vibe_tags") or []) if x],
        avoid_tags=[str(x) for x in (data.get("avoid_tags") or []) if x],
    )
    tw = data.get("time_window") or {}
    return IntentObject(
        explicit_pois=explicit_pois,
        implicit_preferences=prefs,
        date=_as_str(data.get("date")) or "today",
        constraints=Constraints(
            city=_as_str(data.get("city")),
            start=Endpoint(type="named", value=start_place, source="nl_extract") if start_place else Endpoint(type="current", source="default"),
            end=Endpoint(type="named", value=end_place, source="nl_extract") if end_place else None,
            time_window=TimeWindow(start=_as_str(tw.get("start")), end=_as_str(tw.get("end"))),
            transport=_transport(start_data.get("transport_hint")),
        ),
        tasks=tasks,
        fixed_events=fixed_events,
        clarification_needed=[str(x) for x in (data.get("clarification_needed") or []) if x],
    )


def _segments_quality_issue(data: dict) -> str | None:
    if not isinstance(data, dict):
        return "抽取结果不是 JSON 对象"
    segments = data.get("segments")
    if not isinstance(segments, list):
        return "缺少 segments"
    for seg in segments:
        if not isinstance(seg, dict):
            return "segments 中存在非对象"
        place = _as_str(seg.get("place"))
        task = _as_str(seg.get("task")) or ""
        action_residue = (
            "去下", "去一下", "走路去", "驾车去", "到那", "回到", "吃饭", "睡觉",
            "接一下", "找朋友", "访友", "看电影", "我选",
        )
        if place and any(token in place for token in action_residue):
            return f"地点字段疑似混入动作：{place}"
        if place and task and task in place:
            return f"地点字段包含任务：{place}"
    return None


_RETURN_END_RE = re.compile(r"(回到|返回|回家|回住处|回宿舍|回酒店|回公寓|回)\s*")


def _has_return_semantics_near(text: str, place: str) -> bool:
    if not text or not place:
        return False
    idx = text.find(place)
    if idx < 0:
        return False
    window = text[max(0, idx - 10) : idx]
    return bool(_RETURN_END_RE.search(window))


def _end_semantics_issue(message: str, data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    end = data.get("end") or {}
    if not isinstance(end, dict):
        return None
    end_place = normalize_place_name(_as_str(end.get("place")))
    if not end_place or end_place not in message:
        return None
    if _has_return_semantics_near(message, end_place):
        return None
    return (
        f"end.place={end_place} 在原话中没有返程/回家语义。"
        "如果它来自“最后去/最后到/最后前往”，应作为最后一个 segment，"
        "地点放 place，目的放 task，end.place 置空。"
    )


_REMOVAL_MARKERS = ("去掉", "不去", "不用去", "删", "取消", "别去", "不想去", "砍掉", "少去")
_PATCH_MARKERS = (
    "调整", "改", "换", "不是", "我想要的是", "应该是", "附近的", "重新安排", "修正",
    *_REMOVAL_MARKERS,
)


def _is_patch_turn(req: ChatRequest) -> bool:
    if not req.intent:
        return False
    return any(m in req.message for m in _PATCH_MARKERS)


def _anchor_count(intent: IntentObject) -> int:
    """Rough size of a plan, used only to detect a patch that collapsed it."""
    return max(len(intent.explicit_pois), sum(1 for t in intent.tasks if t.needs_poi))


def _place_names(intent: IntentObject) -> set[str]:
    names = {p.name for p in intent.explicit_pois if p.name}
    names |= {t.at for t in intent.tasks if t.at}
    return names


def _merge_patch(prior: IntentObject, patched: IntentObject, message: str) -> IntentObject:
    """Protect multi-turn replanning from a patch that silently wrecks the
    itinerary (the FR-8 failure mode). Always inherit a named start/end the
    model forgot. Then, for a *change* (not a removal), reject the patch and
    keep the prior plan if it either collapsed to one stop OR shares no place
    with the prior — both mean the model rewrote the whole trip (e.g. turning
    三里屯+后海 into 3× the same 国贸 restaurant) instead of editing one stop.
    Robust single-stop editing is the P6 intent-diff (still TODO); this is the
    safety net under it."""
    ps, pe = prior.constraints.start, prior.constraints.end
    qs = patched.constraints.start
    if ps.type == "named" and ps.value and not (qs.type == "named" and qs.value):
        patched.constraints.start = ps
    if pe and pe.value and not (patched.constraints.end and patched.constraints.end.value):
        patched.constraints.end = pe
    removal = any(m in message for m in _REMOVAL_MARKERS)
    if not removal and _anchor_count(prior) >= 2:
        collapsed = _anchor_count(patched) <= 1
        rewrote_everything = bool(_place_names(prior)) and not (_place_names(prior) & _place_names(patched))
        if collapsed or rewrote_everything:
            return prior
    return patched


def _has_preference_choice(message: str) -> bool:
    markers = ("我选", "选择", "偏向", "更想", "按这个", "按这种", "菜系：", "偏好：")
    return any(m in message for m in markers)


def _dining_choice_options(req: ChatRequest, task: Task) -> list[ClarifyOption]:
    base = req.message.strip()
    city_hint = f"在{req.city}" if req.city else ""
    return [
        ClarifyOption(
            id="dining-light",
            label="清淡稳妥",
            description="评分优先，不太油腻，适合不赶时间吃一顿。",
            message=f"{base}\n\n我选：餐厅偏好=清淡稳妥，评分优先，别太油腻。{city_hint}",
        ),
        ClarifyOption(
            id="dining-local",
            label="当地特色",
            description="优先找有地域特色或招牌菜的店。",
            message=f"{base}\n\n我选：餐厅偏好=当地特色/招牌菜，评分优先。{city_hint}",
        ),
        ClarifyOption(
            id="dining-fast",
            label="省时方便",
            description="路线上顺、不排队、出餐快优先。",
            message=f"{base}\n\n我选：餐厅偏好=省时方便，离路线近，不排队，出餐快。{city_hint}",
        ),
    ]


def _mood_choice_options(req: ChatRequest, intent: IntentObject) -> list[ClarifyOption]:
    base = req.message.strip()
    return [
        ClarifyOption(
            id="mood-quiet",
            label="安静放松",
            description="咖啡、书店、公园这类低噪声去处。",
            message=f"{base}\n\n我选：偏好=安静放松，少排队，少走路。",
        ),
        ClarifyOption(
            id="mood-citywalk",
            label="轻量逛逛",
            description="路线顺、可步行衔接，保留一点城市探索感。",
            message=f"{base}\n\n我选：偏好=轻量 city walk，路线顺，不要太累。",
        ),
        ClarifyOption(
            id="mood-view",
            label="景观收尾",
            description="傍晚或夜景作为最后一站。",
            message=f"{base}\n\n我选：偏好=景观收尾，傍晚或夜景，前面安排轻松一点。",
        ),
    ]


def _clarify_options(req: ChatRequest, intent: IntentObject) -> tuple[str, list[ClarifyOption]]:
    if req.scenario or _has_preference_choice(req.message):
        return "", []

    # A clear multi-stop itinerary (or one with fixed meetings) shouldn't be
    # interrupted to ask about restaurant style — just plan it. The dining
    # clarify is only worth a blocking question for light/exploratory asks
    # where the choice actually shapes the whole plan.
    rich_itinerary = (
        bool(intent.fixed_events)
        or len(intent.explicit_pois) >= 2
        or sum(1 for t in intent.tasks if t.needs_poi) >= 3
    )
    generic_food = {"吃饭", "午饭", "晚饭", "餐厅", "饭", "饭菜", "点东西"}
    if not rich_itinerary:
        for task in intent.tasks:
            if task.type == "dining" and (task.intent in generic_food or not task.explicit):
                return "我先确认一下餐厅方向，这会明显影响选店。你更偏向哪一种？", _dining_choice_options(req, task)

    prefs = intent.implicit_preferences
    has_mood = bool(prefs.mood or prefs.vibe_tags or prefs.desired_categories)
    few_anchors = not intent.explicit_pois and len(intent.tasks) <= 1
    if has_mood and few_anchors:
        return "你的需求比较偏感受型，我先给你三个方向，选一个我再落到具体地点。", _mood_choice_options(req, intent)

    return "", []


def _is_plannable(intent: IntentObject) -> bool:
    """Enough signal to build a route: at least one place, task, or hard anchor.
    Non-trip / empty / self-contradictory input lands here as False so we ask
    instead of fabricating a one-stop "行程可行 ✓" plan out of nothing."""
    return bool(intent.explicit_pois or intent.tasks or intent.fixed_events)


_DEFAULT_CLARIFY_Q = (
    "我还没听出具体的行程～你想去哪、想做点什么，"
    "或者说说此刻的心情（比如“有点累，想找个安静的地方”），我来帮你安排。"
)


def _not_plannable_question(intent: Optional[IntentObject]) -> str:
    """Prefer the model's own clarifying questions when it produced them — this
    is exactly the "会问对问题" showcase (FR-2), which the old pipeline threw
    away. Fall back to a friendly default. Bare schema field names (e.g.
    "budget_total") are filtered out so we never surface them to the user."""
    if intent:
        qs = [
            q for q in intent.clarification_needed
            if q and len(q) >= 6 and any("一" <= c <= "鿿" for c in q)
        ]
        if qs:
            return " ".join(qs[:2])
    return _DEFAULT_CLARIFY_Q


async def _extract_intent(req: ChatRequest) -> IntentObject:
    """LLM extraction with a deterministic safety net.

    With a key set we run the cold P1 chain; if the model is slow, down, or
    emits output we cannot repair, we fall back to the heuristic extractor
    instead of failing the whole request (NFR-2). With no key the heuristic is
    the path. This is the difference between "degrades to a simpler plan" and
    "the stream dead-ends with an error" on a flaky network.
    """
    llm = get_llm()
    intent: Optional[IntentObject] = None
    if llm.enabled:
        try:
            intent = await _extract_intent_llm(req, llm)
        except Exception:
            intent = None  # degrade to the heuristic below rather than 500
    if intent is None:
        intent = augment_intent(extract_intent_heuristic(req.message, req.city), req.message)
    # Multi-turn safety net applied to BOTH paths: a patch must not silently
    # destroy the prior itinerary — especially when the LLM patch timed out and
    # we fell back to the (context-free) heuristic here.
    if _is_patch_turn(req) and isinstance(req.intent, IntentObject):
        intent = _merge_patch(req.intent, intent, req.message)
    return intent


async def _extract_intent_llm(req: ChatRequest, llm) -> IntentObject:
    settings = get_settings()
    cold = settings.llm_temperature_cold
    origin_text = (
        f"{req.origin.label or ''} [{req.origin.lng:.4f},{req.origin.lat:.4f}]"
        if req.origin
        else "未知（默认当前位置）"
    )
    is_patch = _is_patch_turn(req)
    if is_patch:
        user = P1_PATCH_USER.format(
            intent=req.intent.model_dump_json() if hasattr(req.intent, "model_dump_json") else json.dumps(req.intent, ensure_ascii=False),
            message=req.message,
            city=req.city or "未知",
            origin=origin_text,
            history=_format_history(req),
        )
        draft = await llm.complete_json(P1_PATCH_SYSTEM, user, cold)
    else:
        user = P1_SEGMENTS_USER.format(
            message=req.message,
            city=req.city or "未知",
            origin=origin_text,
            history=_format_history(req),
        )
        draft = await llm.complete_json(P1_SEGMENTS_SYSTEM, user, cold)

    # Audit + self-repair (§6.4.4) only when we actually detect a problem,
    # instead of paying a second round-trip on every clean extraction. The
    # old code always ran one extra repair call — doubling latency (NFR-1).
    issue = _segments_quality_issue(draft) or _end_semantics_issue(req.message, draft)
    if issue:
        repair_user = P1_REPAIR_USER.format(
            message=req.message,
            draft=json.dumps(draft, ensure_ascii=False),
            issue=issue,
        )
        draft = await llm.complete_json(P1_REPAIR_SYSTEM, repair_user, cold)
    # Patch-merge is applied by the caller (_extract_intent) so it also covers
    # the heuristic fallback when this LLM path raises.
    return _intent_from_segments(draft)


async def plan_stream(req: ChatRequest) -> AsyncIterator[StreamEvent]:
    settings = get_settings()
    try:
        # --- 0. empty input -> ask, don't invent a trip ------------------
        if not (req.message or "").strip() and not req.scenario:
            yield _ev(type="clarify", text=_DEFAULT_CLARIFY_Q, options=[])
            yield _ev(type="done")
            return

        # --- 1. preset short-circuit (stable demo) -----------------------
        # Only the preset chips (explicit scenario) replay the canned plan.
        # Free text — even when it happens to contain a couple of demo
        # keywords like "安静"+"望京" — always runs the real chain, so we never
        # hand the user a fabricated itinerary that ignores what they asked.
        scenario = req.scenario
        if scenario:
            demo = get_demo(scenario)
            if demo:
                plan, narration = demo
                yield _ev(type="thinking", text="正在理解你的需求 🧭")
                await asyncio.sleep(0.35)
                yield _ev(type="understanding", understanding=plan.understanding)
                yield _ev(type="thinking", text="已找到真实候选、核对营业时间与路程…")
                await asyncio.sleep(0.35)
                yield _ev(type="plan", plan=plan)
                async for chunk in _emit_paced(narration):
                    yield chunk
                yield _ev(type="done")
                return

        # --- 2. live chain ----------------------------------------------
        yield _ev(type="thinking", text="正在理解你的需求 🧭")
        intent = await _extract_intent(req)

        # Non-trip / contradictory / empty-after-parse input: ask the right
        # question instead of fabricating a degenerate plan. The model often
        # already wrote a good question into clarification_needed (FR-2).
        if not _is_plannable(intent):
            yield _ev(type="clarify", text=_not_plannable_question(intent), options=[], intent=intent)
            yield _ev(type="done")
            return

        understanding: Understanding = build_understanding(intent)
        yield _ev(type="understanding", understanding=understanding)
        yield _ev(type="thinking", text=_extraction_detail(intent))
        await asyncio.sleep(0.15)

        question, options = _clarify_options(req, intent)
        if options:
            yield _ev(type="clarify", text=question, options=options, intent=intent)
            yield _ev(type="done")
            return

        yield _ev(type="thinking", text="正在用高德检索真实地点…")
        city = intent.constraints.city or req.city or settings.default_city
        origin = [req.origin.lng, req.origin.lat] if req.origin else None
        plan = await build_plan(intent, origin, city)
        yield _ev(type="thinking", text="已拿到候选地点，正在计算分段路程和停留时间…")
        await asyncio.sleep(0.15)
        yield _ev(type="thinking", text="路线和时间轴已生成，正在绘制地图…")
        yield _ev(type="plan", plan=plan)

        # --- 3. P5 narration (warm, streamed) ---------------------------
        llm = get_llm()
        if llm.enabled:
            try:
                user = P5_NARRATE_USER.format(
                    intent_summary=_intent_summary(intent),
                    itinerary_text=_itinerary_text(plan),
                )
                async for delta in llm.stream_text(
                    P5_NARRATE_SYSTEM, user, settings.llm_temperature_warm
                ):
                    yield _ev(type="message", text=delta)
            except Exception:
                async for chunk in _emit_paced(_fallback_narration(plan)):
                    yield chunk
        else:
            async for chunk in _emit_paced(_fallback_narration(plan)):
                yield chunk

        yield _ev(type="done")
    except Exception as exc:  # never leave the stream hanging
        yield _ev(type="error", text=f"规划时出错了：{exc}")
        yield _ev(type="done")
