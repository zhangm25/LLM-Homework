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
from ..debug_log import write_debug_log
from ..llm.client import get_llm
from ..llm.prompts import (
    P1_INTENT_SYSTEM,
    P1_INTENT_USER,
    P1_CLARIFY_SYSTEM,
    P1_CLARIFY_USER,
    P1_PATCH_SYSTEM,
    P1_PATCH_USER,
    P1_REPAIR_SYSTEM,
    P1_REPAIR_USER,
    P1_SEGMENTS_SYSTEM,
    P1_SEGMENTS_USER,
    P1_VALIDATE_SYSTEM,
    P1_VALIDATE_USER,
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
from ..planner.scheduler import build_plan, build_understanding, _resolve_named
from .heuristics import augment_intent, extract_intent_heuristic
from .place_resolution import resolve_place_slots
from .place_norm import normalize_place_name

STREAM_DELAY = 0.02  # pacing for non-LLM narration, gives a "typing" feel
NARRATION_CHUNK = 2  # characters per emitted chunk in fallback mode


def _debug_state(label: str, **data) -> None:
    if not get_settings().llm_debug_log:
        return
    safe = {}
    for key, value in data.items():
        if hasattr(value, "model_dump"):
            safe[key] = value.model_dump()
        else:
            safe[key] = value
    write_debug_log(
        f"========== PIPELINE STATE [{label}] ==========\n"
        f"{json.dumps(safe, ensure_ascii=False, default=str, indent=2)[:8000]}\n"
        f"========== END PIPELINE STATE [{label}] =========="
    )


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


def _as_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"false", "0", "no", "否", "不", "不可用"}:
            return False
        if text in {"true", "1", "yes", "是", "可用"}:
            return True
    return bool(value)


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
    clarification_needed = [str(x) for x in (data.get("clarification_needed") or []) if x]
    is_available = _as_bool(data.get("is_available"), True) and not clarification_needed
    return IntentObject(
        is_available=is_available,
        pending_question_type=_as_str(data.get("pending_question_type")),
        pending_field=_as_str(data.get("pending_field")),
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
        clarification_needed=clarification_needed,
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
    "加一个", "加上", "再加", "顺便", "还有", "另外", "然后", "接着", "刚才", "上一轮", "前面",
    *_REMOVAL_MARKERS,
)


def _is_patch_turn(req: ChatRequest) -> bool:
    if not req.intent:
        return False
    if isinstance(req.intent, IntentObject) and req.intent.is_available:
        return True
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


def _is_clarification_turn(req: ChatRequest) -> bool:
    return isinstance(req.intent, IntentObject) and not req.intent.is_available


def _mark_unavailable(intent: IntentObject, question: str) -> IntentObject:
    intent.is_available = False
    if question and question not in intent.clarification_needed:
        intent.clarification_needed = [question, *intent.clarification_needed]
    return intent


def _mark_pending(intent: IntentObject, issue: dict | None) -> IntentObject:
    if not issue:
        intent.pending_question_type = "general"
        intent.pending_field = None
        return intent
    code = str(issue.get("code") or "")
    field = _as_str(issue.get("field"))
    pending = "general"
    if "start" in code or field == "start":
        pending = "start"
    elif "end" in code or field == "end":
        pending = "end"
    elif "place" in code:
        pending = "place_candidate"
    elif "preference" in code or "dining" in code:
        pending = "preference"
    intent.pending_question_type = pending
    intent.pending_field = field
    return intent


def _option_dicts(options: list[ClarifyOption]) -> list[dict]:
    return [o.model_dump() for o in options]


def _issue(
    code: str,
    field: str,
    severity: str,
    evidence: str,
    question: str = "",
    options: list[ClarifyOption] | None = None,
) -> dict:
    return {
        "code": code,
        "field": field,
        "severity": severity,
        "evidence": evidence,
        "fallback_question": question,
        "fallback_options": _option_dicts(options or []),
    }


def _merge_clarification_answer(prior: IntentObject, message: str) -> IntentObject:
    """Small deterministic bridge for clicked clarify options and keyless mode.
    The LLM path uses P1_CLARIFY; this covers "我选：终点是 X" without reparsing
    the original request or looping on the same question."""
    text = message or ""
    merged = prior.model_copy(deep=True)
    m = re.search(r"我选：\s*起点是\s*([^\n，,。；;]+)", text)
    if m:
        value = normalize_place_name(m.group(1).strip()) or m.group(1).strip()
        if value:
            merged.constraints.start = Endpoint(type="named", value=value, source="nl_extract")
    m = re.search(r"我选：\s*终点是\s*([^\n，,。；;]+)", text)
    if m:
        value = normalize_place_name(m.group(1).strip()) or m.group(1).strip()
        if value:
            merged.constraints.end = Endpoint(type="named", value=value, source="nl_extract")
    m = re.search(r"我选：\s*(?:餐厅偏好|偏好)\s*=\s*([^\n，,。；;]+)", text)
    if m:
        pref = m.group(1).strip()
        if pref and pref not in merged.implicit_preferences.vibe_tags:
            merged.implicit_preferences.vibe_tags.append(pref)
    if not _has_preference_choice(text):
        answer = text.strip()
        answer = re.sub(r"^我选：\s*", "", answer).strip()
        if answer and "\n" not in answer and len(answer) <= 80:
            if prior.pending_question_type == "start":
                value = normalize_place_name(answer) or answer
                merged.constraints.start = Endpoint(type="named", value=value, source="nl_extract")
            elif prior.pending_question_type == "end":
                value = normalize_place_name(answer) or answer
                merged.constraints.end = Endpoint(type="named", value=value, source="nl_extract")
    if merged.constraints.start.value or (merged.constraints.end and merged.constraints.end.value) or _has_preference_choice(text):
        merged.is_available = True
        merged.clarification_needed = []
        merged.pending_question_type = None
        merged.pending_field = None
    return merged


_GENERIC_RETURN_ENDPOINTS = {
    "家", "家里", "住处", "住的地方", "宿舍", "酒店", "宾馆", "民宿", "公寓",
    "公司", "单位", "办公室",
}
_GENERIC_RETURN_ONLY_RE = re.compile(
    r"(回家|回去|回住处|回宿舍|回酒店|回宾馆|回民宿|回公寓|回公司|回单位|回办公室|"
    r"回到家|回到住处|回到宿舍|回到酒店|回到宾馆|回到民宿|回到公寓|回到公司|回到单位|回到办公室|"
    r"返回住处|返回酒店|返回宾馆|返回民宿|返回宿舍|返回公寓|返回公司|返回单位)"
)
_QUALIFIED_GENERIC_RETURN_RE = re.compile(
    r"(?:回到|返回|回)\s*([一-龥A-Za-z0-9·]{1,18}?)的"
    r"(家|住处|宿舍|酒店|宾馆|民宿|公寓|公司|单位|办公室)"
)
_BARE_RETURN_PLACE_RE = re.compile(r"(?:回到|返回|回)\s*([一-龥A-Za-z0-9·A-Za-z]{2,24}?)(?:，|,|。|；|;| |$)")
_PRONOUN_RETURN_RE = re.compile(r"(?:回到|返回|回)\s*(这里|这儿|那里|那儿|原处)")
_SPECIFIC_ENDPOINT_HINTS = (
    "酒店", "宾馆", "民宿", "旅馆", "客栈", "小区", "公寓", "宿舍", "家属院", "社区",
    "大厦", "大楼", "写字楼", "中心", "广场", "商场", "园区", "校区", "医院", "学校",
    "大学", "中学", "小学", "公司", "门店", "餐厅", "饭店", "咖啡", "书店", "公园",
    "地铁站", "火车站", "机场", "车站", "东门", "西门", "南门", "北门", "SOHO",
)
_SCHOOL_PLACE_TOKENS = ("大学", "学院", "学校", "中学", "小学")
_SCHOOL_DETAIL_TOKENS = ("校区", "东门", "西门", "南门", "北门", "楼", "园区", "附中", "附小")
_VAGUE_CURRENT_START_RE = re.compile(
    r"(?:从|由|自)\s*(?:这里|这儿|当前位置|当前的位置|我这|附近)\s*(?:出发|开始|走|去|到)?|"
    r"(?:我在|现在在|目前在|人在)\s*(?:这里|这儿|当前位置|当前的位置|我这|附近)"
)


def _ambiguous_return_phrase(message: str, intent: IntentObject) -> str | None:
    text = message or ""
    m = _QUALIFIED_GENERIC_RETURN_RE.search(text)
    if m:
        return f"{m.group(1)}的{m.group(2)}"
    m = _GENERIC_RETURN_ONLY_RE.search(text) or _PRONOUN_RETURN_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_RETURN_PLACE_RE.search(text)
    if m:
        place = normalize_place_name(m.group(1))
        if place and not any(hint in place for hint in _SPECIFIC_ENDPOINT_HINTS):
            return place
    end = intent.constraints.end
    value = normalize_place_name(end.value) if end and end.value else None
    if value in _GENERIC_RETURN_ENDPOINTS:
        return value
    return None


def _origin_context_text(req: ChatRequest) -> str:
    if req.origin:
        label = req.origin.label or "浏览器定位"
        return f"可用：{label} [{req.origin.lng:.4f},{req.origin.lat:.4f}]"
    status_text = {
        "denied": "unknown（用户拒绝了浏览器定位权限）",
        "unsupported": "unknown（浏览器不支持定位）",
        "unavailable": "unknown（浏览器暂时无法取得位置）",
        "timeout": "unknown（浏览器定位超时）",
        "available": "unknown（前端标记可用但未附带坐标）",
        "unknown": "unknown（用户当前位置未提供）",
    }
    return status_text.get(req.origin_status, "unknown（用户当前位置未提供）")


def _apply_origin_to_start(req: ChatRequest, intent: IntentObject) -> IntentObject:
    """Bind browser geolocation to the start endpoint before routing.

    LLMs often shorten a long reverse-geocoded address ("北京市海淀区...清华附中")
    to a POI name ("清华大学附属中学"). That is pleasant prose but bad routing:
    text geocoding can become ambiguous. If the named start clearly came from
    the browser origin label, keep the full label and exact coordinates.
    """
    if not req.origin:
        return intent
    label = (req.origin.label or "").strip()
    loc = [req.origin.lng, req.origin.lat]
    start = intent.constraints.start
    if start.type == "current":
        start.source = "geolocation"
        start.location = loc
        if label:
            start.value = label
        return intent
    value = (start.value or "").strip()
    if label and value and (value in label or label in value):
        start.value = label
        start.source = "geolocation"
        start.location = loc
    elif start.source == "geolocation":
        start.location = loc
        if label and not start.value:
            start.value = label
    return intent


def _start_clarification(req: ChatRequest, intent: IntentObject) -> tuple[str, list[ClarifyOption]] | None:
    """When the user explicitly says "from here" but the browser location is not
    available, ask instead of quietly falling back to the city centre."""
    if _has_clicked_place_choice(req.message, "start") or req.origin or not _VAGUE_CURRENT_START_RE.search(req.message or ""):
        return None
    start = intent.constraints.start
    if start.type == "named" and start.value:
        return None
    base = req.message.strip()
    options: list[ClarifyOption] = []
    first_place = next((p.name for p in intent.explicit_pois if p.name), None)
    if first_place:
        options.append(ClarifyOption(
            id="start-first-place",
            label=f"从 {first_place}",
            description="把行程中第一个明确地点当作出发点。",
            message=f"{base}\n\n我选：起点是 {first_place}",
        ))
    question = (
        "我还拿不到你的当前位置，所以「从这里」暂时不可导航。"
        + ("你可以点一个猜测，或直接在输入框里补充具体起点。" if options else "请直接补充具体起点。")
    )
    return question, options


def _start_issue(req: ChatRequest, intent: IntentObject) -> dict | None:
    clarify = _start_clarification(req, intent)
    if not clarify:
        return None
    question, options = clarify
    question = (
        "我现在拿不到你的当前位置（浏览器定位不可用），所以还不能确定真实出发点。"
        "你可以开启定位后重试，或者直接告诉我你现在所在的小区、楼宇、校门、地标或详细地址。"
    )
    return _issue(
        "current_start_unknown",
        "start",
        "blocking",
        "用户把「这里/当前位置」作为起点，但浏览器定位不可用；需要用户开启定位或补充当前具体位置。",
        question,
        options,
    )


def _end_clarification(req: ChatRequest, intent: IntentObject) -> tuple[str, list[ClarifyOption]] | None:
    if _has_clicked_place_choice(req.message, "end"):
        return None
    phrase = _ambiguous_return_phrase(req.message, intent)
    if not phrase:
        return None
    base = req.message.strip()
    options: list[ClarifyOption] = []
    start = intent.constraints.start
    if start.type == "named" and start.value:
        options.append(ClarifyOption(
            id="end-start",
            label="回到起点",
            description=f"终点按「{start.value}」处理。",
            message=f"{base}\n\n我选：终点是 {start.value}",
        ))
    elif req.origin:
        label = req.origin.label or "当前位置"
        options.append(ClarifyOption(
            id="end-origin",
            label="回到当前位置",
            description=label,
            message=f"{base}\n\n我选：终点是 {label}",
        ))
    question = (
        f"你提到最后要回「{phrase}」，但这还不是一个可导航的具体终点。"
        + (
            "我可以先按下面的猜测继续，或者你直接补充小区/大厦/酒店名/门牌或附近地标。"
            if options
            else "请直接补充小区/大厦/酒店名/门牌或附近地标。"
        )
    )
    return question, options


def _end_issue(req: ChatRequest, intent: IntentObject) -> dict | None:
    clarify = _end_clarification(req, intent)
    if not clarify:
        return None
    question, options = clarify
    phrase = _ambiguous_return_phrase(req.message, intent) or ""
    return _issue(
        "ambiguous_end",
        "end",
        "blocking",
        f"用户提到返回「{phrase}」，但没有具体可导航终点。",
        question,
        options,
    )


def _has_clicked_place_choice(message: str, role: str) -> bool:
    label = "起点" if role == "start" else "终点"
    return "我选：" in (message or "") and f"{label}是" in message


def _dedup_pois(pois) -> list:
    seen: set[tuple[str, str, tuple[float, float]]] = set()
    out = []
    for p in pois:
        key = (p.name, p.address, (round(p.location[0], 6), round(p.location[1], 6)))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _needs_school_choice(place: str) -> bool:
    return (
        bool(place)
        and any(tok in place for tok in _SCHOOL_PLACE_TOKENS)
        and not any(tok in place for tok in _SCHOOL_DETAIL_TOKENS)
    )


def _place_choice_options(req: ChatRequest, role: str, place: str, pois) -> list[ClarifyOption]:
    base = req.message.strip()
    label = "起点" if role == "start" else "终点"
    options: list[ClarifyOption] = []
    for i, p in enumerate(pois[:4], start=1):
        address = p.address or "地址未标注"
        options.append(ClarifyOption(
            id=f"{role}-place-{i}",
            label=p.name,
            description=address,
            message=f"{base}\n\n我选：{label}是 {p.name} {address}",
        ))
    return options


async def _place_choice_clarify(req: ChatRequest, intent: IntentObject) -> tuple[str, list[ClarifyOption]] | None:
    city = intent.constraints.city or req.city or get_settings().default_city
    anchors: list[tuple[str, str]] = []
    start = intent.constraints.start
    if (
        start.type == "named"
        and start.value
        and start.source != "geolocation"
        and not start.location
        and not _has_clicked_place_choice(req.message, "start")
    ):
        anchors.append(("start", start.value))
    end = intent.constraints.end
    if (
        end
        and end.value
        and end.source != "geolocation"
        and not end.location
        and not _has_clicked_place_choice(req.message, "end")
    ):
        anchors.append(("end", end.value))

    for role, place in anchors:
        if not _needs_school_choice(place):
            continue
        pois = _dedup_pois(await _resolve_named(place, city))
        if len(pois) < 2:
            continue
        label = "起点" if role == "start" else "终点"
        question = f"「{place}」有多个校区/地址。请确认你说的{label}是哪一个？"
        return question, _place_choice_options(req, role, place, pois)
    return None


async def _place_choice_issue(req: ChatRequest, intent: IntentObject) -> dict | None:
    clarify = await _place_choice_clarify(req, intent)
    if not clarify:
        return None
    question, options = clarify
    return _issue(
        "ambiguous_place_candidate",
        "start_or_end",
        "blocking",
        "起点或终点命中多个真实候选，需要确认具体校区/地址。",
        question,
        options,
    )


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


def _preference_issue(req: ChatRequest, intent: IntentObject) -> dict | None:
    question, options = _clarify_options(req, intent)
    if not options:
        return None
    code = "preference_choice"
    field = "preferences"
    if options and options[0].id.startswith("dining"):
        code = "dining_preference_unspecified"
        field = "tasks"
    return _issue(
        code,
        field,
        "advisory",
        "偏好会影响候选选择，但通常可以由模型和默认策略先行推断。",
        question,
        options,
    )


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


async def _validation_issues(req: ChatRequest, intent: IntentObject) -> list[dict]:
    issues: list[dict] = []
    for item in (_start_issue(req, intent), _end_issue(req, intent)):
        if item:
            issues.append(item)
    place = await _place_choice_issue(req, intent)
    if place:
        issues.append(place)
    return issues


def _fallback_issue_clarify(intent: IntentObject, issues: list[dict]) -> tuple[str, list[ClarifyOption], IntentObject] | None:
    blocking = next((i for i in issues if i.get("severity") == "blocking"), None)
    if not blocking:
        return None
    question = _as_str(blocking.get("fallback_question")) or _DEFAULT_CLARIFY_Q
    options = [
        ClarifyOption(**o)
        for o in (blocking.get("fallback_options") or [])
        if isinstance(o, dict) and o.get("id") and o.get("label") and o.get("message")
    ]
    return question, options, _mark_pending(_mark_unavailable(intent, question), blocking)


def _intent_from_validation_patch(raw_intent, fallback: IntentObject) -> IntentObject:
    if not isinstance(raw_intent, dict):
        return fallback
    # P1_VALIDATE may return either the segment schema or the internal IntentObject
    # schema. Accept both so the judge prompt can stay compact.
    if "segments" in raw_intent:
        return _intent_from_segments(raw_intent)
    try:
        return IntentObject.model_validate(raw_intent)
    except Exception:
        return fallback


async def _validate_with_llm(req: ChatRequest, intent: IntentObject, issues: list[dict], llm) -> tuple[str, list[ClarifyOption], IntentObject] | IntentObject | None:
    settings = get_settings()
    user = P1_VALIDATE_USER.format(
        message=req.message,
        intent=intent.model_dump_json(),
        issues=json.dumps(issues, ensure_ascii=False),
        city=req.city or "未知",
        origin=_origin_context_text(req),
        history=_format_history(req),
    )
    _debug_state("validation:llm_request", issues=issues, intent=intent)
    data = await llm.complete_json(P1_VALIDATE_SYSTEM, user, settings.llm_temperature_cold, stage="P1_VALIDATE")
    _debug_state("validation:llm_response", response=data)
    action = _as_str(data.get("action")) or "proceed"
    if action == "patch_intent":
        return augment_intent(_intent_from_validation_patch(data.get("intent"), intent), req.message)
    if action == "ask_user":
        question = _as_str(data.get("question")) or _DEFAULT_CLARIFY_Q
        options = []
        for raw in data.get("options") or []:
            if isinstance(raw, dict) and raw.get("id") and raw.get("label") and raw.get("message"):
                options.append(ClarifyOption(
                    id=str(raw.get("id")),
                    label=str(raw.get("label")),
                    description=str(raw.get("description") or ""),
                    message=str(raw.get("message")),
                ))
        first_issue = issues[0] if issues else None
        return question, options, _mark_pending(_mark_unavailable(intent, question), first_issue)
    return intent


async def _resolve_validation(req: ChatRequest, intent: IntentObject) -> tuple[str, list[ClarifyOption], IntentObject] | IntentObject:
    issues = await _validation_issues(req, intent)
    _debug_state("validation:issues", issues=issues)
    if not issues:
        return intent
    llm = get_llm()
    if llm.enabled:
        try:
            decision = await _validate_with_llm(req, intent, issues, llm)
            if decision is not None:
                return decision
        except Exception:
            _debug_state("validation:llm_failed")
            pass
    fallback = _fallback_issue_clarify(intent, issues)
    if fallback:
        question, options, pending = fallback
        _debug_state("validation:fallback_ask", question=question, options=options, intent=pending)
    else:
        _debug_state("validation:fallback_proceed")
    return fallback if fallback else intent


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
    _debug_state(
        "extract_intent:start",
        message=req.message,
        has_prior_intent=isinstance(req.intent, IntentObject),
        prior_available=req.intent.is_available if isinstance(req.intent, IntentObject) else None,
        llm_enabled=llm.enabled,
    )
    if _is_clarification_turn(req):
        prior = req.intent
        _debug_state(
            "extract_intent:route",
            route="P1_CLARIFY",
            pending_question_type=prior.pending_question_type,
            pending_field=prior.pending_field,
            clarification_needed=prior.clarification_needed,
        )
        if llm.enabled:
            try:
                intent = await _complete_pending_intent_llm(req, prior, llm)
            except Exception:
                _debug_state("extract_intent:llm_failed", route="P1_CLARIFY")
                intent = None
        if intent is None:
            intent = _merge_clarification_answer(prior, req.message)
            _debug_state("extract_intent:fallback", route="P1_CLARIFY", intent=intent)
        intent = _apply_origin_to_start(req, augment_intent(intent, req.message))
        _debug_state("extract_intent:done", route="P1_CLARIFY", intent=intent)
        return intent

    if llm.enabled:
        try:
            _debug_state(
                "extract_intent:route",
                route="P1_PATCH" if _is_patch_turn(req) else "P1_SEGMENTS",
            )
            intent = await _extract_intent_llm(req, llm)
        except Exception:
            _debug_state(
                "extract_intent:llm_failed",
                route="P1_PATCH" if _is_patch_turn(req) else "P1_SEGMENTS",
            )
            intent = None  # degrade to the heuristic below rather than 500
    if intent is None:
        intent = augment_intent(extract_intent_heuristic(req.message, req.city), req.message)
        _debug_state("extract_intent:fallback", route="heuristic", intent=intent)
    else:
        intent = augment_intent(intent, req.message)
    # Multi-turn safety net applied to BOTH paths: a patch must not silently
    # destroy the prior itinerary — especially when the LLM patch timed out and
    # we fell back to the (context-free) heuristic here.
    if isinstance(req.intent, IntentObject):
        intent = _merge_patch(req.intent, intent, req.message)
    intent = _apply_origin_to_start(req, intent)
    _debug_state("extract_intent:done", intent=intent)
    return intent


async def _complete_pending_intent_llm(req: ChatRequest, prior: IntentObject, llm) -> IntentObject:
    settings = get_settings()
    user = P1_CLARIFY_USER.format(
        intent=prior.model_dump_json(),
        questions="\n".join(prior.clarification_needed) or "（无）",
        message=req.message,
        city=req.city or "未知",
        origin=_origin_context_text(req),
        history=_format_history(req),
    )
    draft = await llm.complete_json(P1_CLARIFY_SYSTEM, user, settings.llm_temperature_cold, stage="P1_CLARIFY")
    return _intent_from_validation_patch(draft, prior)


async def _extract_intent_llm(req: ChatRequest, llm) -> IntentObject:
    settings = get_settings()
    cold = settings.llm_temperature_cold
    origin_text = _origin_context_text(req)
    is_patch = _is_patch_turn(req)
    if is_patch:
        user = P1_PATCH_USER.format(
            intent=req.intent.model_dump_json() if hasattr(req.intent, "model_dump_json") else json.dumps(req.intent, ensure_ascii=False),
            message=req.message,
            city=req.city or "未知",
            origin=origin_text,
            history=_format_history(req),
        )
        draft = await llm.complete_json(P1_PATCH_SYSTEM, user, cold, stage="P1_PATCH")
    else:
        user = P1_SEGMENTS_USER.format(
            message=req.message,
            city=req.city or "未知",
            origin=origin_text,
            intent=(
                req.intent.model_dump_json()
                if isinstance(req.intent, IntentObject)
                else json.dumps(req.intent, ensure_ascii=False) if req.intent else "（无）"
            ),
            history=_format_history(req),
        )
        draft = await llm.complete_json(P1_SEGMENTS_SYSTEM, user, cold, stage="P1_SEGMENTS")

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
        draft = await llm.complete_json(P1_REPAIR_SYSTEM, repair_user, cold, stage="P1_REPAIR")
    # Patch-merge is applied by the caller (_extract_intent) so it also covers
    # the heuristic fallback when this LLM path raises. Accept either the
    # segment schema or the internal IntentObject schema; smaller models
    # sometimes mirror the schema they saw in the user prompt.
    return _intent_from_validation_patch(draft, IntentObject())


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
        _debug_state(
            "plan_stream:start",
            message=req.message,
            city=req.city,
            origin=req.origin,
            origin_status=req.origin_status,
            has_intent=isinstance(req.intent, IntentObject),
            scenario=req.scenario,
        )
        intent = await _extract_intent(req)
        _debug_state("plan_stream:after_extract", intent=intent)

        if not intent.is_available and intent.clarification_needed:
            _debug_state(
                "plan_stream:clarify_from_llm",
                text=" ".join(intent.clarification_needed[:2]),
                intent=intent,
            )
            yield _ev(type="clarify", text=" ".join(intent.clarification_needed[:2]), options=[], intent=intent)
            yield _ev(type="done")
            return

        validation = await _resolve_validation(req, intent)
        if isinstance(validation, tuple):
            question, options, pending_intent = validation
            _debug_state("plan_stream:clarify_from_validation", text=question, options=options, intent=pending_intent)
            yield _ev(type="clarify", text=question, options=options, intent=pending_intent)
            yield _ev(type="done")
            return
        intent = validation
        _debug_state("plan_stream:after_validation", intent=intent)

        # Non-trip / contradictory / empty-after-parse input: ask the right
        # question instead of fabricating a degenerate plan. The model often
        # already wrote a good question into clarification_needed (FR-2).
        if not _is_plannable(intent):
            question = _not_plannable_question(intent)
            intent = _mark_unavailable(intent, question)
            _debug_state("plan_stream:not_plannable", text=question, intent=intent)
            yield _ev(type="clarify", text=question, options=[], intent=intent)
            yield _ev(type="done")
            return

        understanding: Understanding = build_understanding(intent)
        yield _ev(type="understanding", understanding=understanding)
        yield _ev(type="thinking", text=_extraction_detail(intent))
        await asyncio.sleep(0.15)

        yield _ev(type="thinking", text="正在用高德检索真实地点…")
        city = intent.constraints.city or req.city or settings.default_city
        origin = [req.origin.lng, req.origin.lat] if req.origin else None
        place_resolution = await resolve_place_slots(intent, origin, city)
        _debug_state("plan_stream:place_resolution", city=city, origin=origin, resolution=place_resolution, intent=intent)
        _debug_state("plan_stream:build_plan", city=city, origin=origin, intent=intent)
        plan = await build_plan(intent, origin, city)
        plan.place_resolution = place_resolution
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
                    P5_NARRATE_SYSTEM, user, settings.llm_temperature_warm, stage="P5_NARRATE"
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
