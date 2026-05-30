"""Regression tests for the RoamMind fixes (run: python backend/tests/test_roammind.py).

No pytest dependency: a tiny runner executes every ``check_*`` coroutine/function
and reports PASS/FAIL, exiting non-zero on any failure. A FakeAMap makes the
grounding + scheduling deterministic and offline, so these tests don't spend
DeepSeek/AMap quota and don't flake on the network.

Each check maps to a defect found during ID/OOD testing — see the report.
"""
from __future__ import annotations

import asyncio
import os
import sys
import zipfile
from pathlib import Path
from io import BytesIO

# Force the keyless (heuristic) path for pipeline tests; AMap is faked per-test.
os.environ["LLM_API_KEY"] = ""
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["AMAP_WEB_SERVICE_KEY"] = ""
os.environ["LLM_DEBUG_LOG"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../backend

import app.planner.scheduler as sch  # noqa: E402
from app.planner.scheduler import (  # noqa: E402
    _base_name, _name_related, _is_landmark, _is_facility, _pick_main_poi,
    _extract_hhmm, _haversine_m, build_plan, _date_offset, _earliest_anchor,
    build_understanding, recompute_plan,
)
from app.tools.amap_client import POI, Leg  # noqa: E402
from app.models.intent import (  # noqa: E402
    IntentObject, Constraints, Endpoint, Task, FixedEvent, TimeWindow, ExplicitPOI,
    ImplicitPreferences,
)
from app.models.plan import ChatRequest, GeoPoint  # noqa: E402
from app.agent.pipeline import (  # noqa: E402
    plan_stream, _is_plannable, _not_plannable_question, _merge_patch, _anchor_count,
    _extract_intent, _complete_pending_intent_llm, _apply_origin_to_start,
    _allow_nonblocking_llm_clarification,
)
from app.agent.place_resolution import resolve_place_slots  # noqa: E402
from app.tools.file_parser import parse_attachment  # noqa: E402


# --------------------------------------------------------------------------
# FakeAMap — deterministic, offline. Named places resolve from _GEO; "near"
# searches return a POI at the anchor; specific keywords can be scripted.
# --------------------------------------------------------------------------
_GEO = {
    "上海中心": [121.5055, 31.2353], "浦东软件园": [121.6010, 31.2030],
    "上海中心大厦": [121.5055, 31.2353], "上海瑞金洲际酒店": [121.4660, 31.2160],
    "上海锦江汤臣洲际大酒店": [121.5350, 31.2350],
    "望京": [116.4709, 39.9966], "国贸": [116.4610, 39.9088],
    "清华大学": [116.3269, 40.0032], "五道口": [116.3373, 39.9929],
}


def _poi(name, loc, type_="", rating=None, address=""):
    return POI(id=name, name=name, address=address, location=list(loc), type=type_, rating=rating)


def _hash_loc(kw: str) -> list[float]:
    h = sum(ord(c) for c in kw)
    return [116.40 + (h % 50) / 1000.0, 39.90 + (h % 37) / 1000.0]


class FakeAMap:
    enabled = True

    def __init__(self, scripted: dict[str, list[POI]] | None = None):
        self.scripted = scripted or {}

    async def search_poi_text(self, keywords, region=None, types=None, page_size=10):
        if keywords in self.scripted:
            return self.scripted[keywords][:page_size]
        loc = _GEO.get(keywords) or _hash_loc(keywords)
        return [_poi(keywords, loc)]

    async def search_poi_around(self, keywords, location, radius_m=3000, types=None,
                                sortrule="weight", page_size=10):
        if keywords in self.scripted:
            return self.scripted[keywords][:page_size]
        return [_poi(keywords, [location[0] + 0.001, location[1]], rating=4.5)]

    async def _route(self, a, b, mode, kmh):
        d = _haversine_m(a, b)
        return Leg(mode=mode, distance_m=int(d), duration_s=int(d / (kmh * 1000 / 3600)), polyline=[a, b])

    async def route_walking(self, a, b):
        return await self._route(a, b, "walking", 5)

    async def route_driving(self, a, b, waypoints=None):
        return await self._route(a, b, "driving", 30)

    async def aclose(self):
        pass


def use_fake(scripted=None):
    sch.get_amap = lambda: FakeAMap(scripted)  # scheduler resolves get_amap at call time


# --------------------------------------------------------------------------
# Group 1 — pure POI helpers (the grounding-correctness fixes)
# --------------------------------------------------------------------------
def check_base_name():
    assert _base_name("新辰里购物中心(亚运村店)") == "新辰里购物中心"
    assert _base_name("国家体育场（鸟巢）") == "国家体育场"
    assert _base_name("玉渊潭公园") == "玉渊潭公园"


def check_name_related():
    assert _name_related("玉渊潭公园", "玉渊潭公园")
    assert _name_related("国家图书馆", "中国国家图书馆")          # contained
    assert _name_related("新辰里购物中心", "新辰里购物中心(亚运村店)")  # branch
    assert not _name_related("霍格沃茨魔法学校", "明月魔法学院")     # fictional -> unrelated
    assert not _name_related("北京科技大学南门", "正宗南门涮肉(颐和园店)")
    assert not _name_related("新辰里购物中心", "新中关购物中心")     # wrong mall


def check_landmark_and_facility():
    assert _is_landmark(_poi("国家体育场", [0, 0], "体育休闲服务;运动场馆;综合体育馆"))
    assert not _is_landmark(_poi("明月魔法学院", [0, 0], "体育休闲服务;休闲场所;休闲场所"))
    assert _is_facility(_poi("新辰里购物中心停车场(出入口)", [0, 0], "交通设施服务;停车场;停车场出入口"))
    assert not _is_facility(_poi("新辰里购物中心(亚运村店)", [0, 0], "购物服务;商场;购物中心"))


def check_pick_main_poi_branch():
    # The real mall has "店" in its branch suffix; it must NOT be rejected, and a
    # parking-lot entrance must not win.
    cands = [
        _poi("新辰里购物中心停车场(出入口)", [1, 1], "交通设施服务;停车场;停车场出入口"),
        _poi("新中关购物中心", [2, 2], "购物服务;商场;购物中心"),
        _poi("新辰里购物中心(亚运村店)", [3, 3], "购物服务;商场;购物中心"),
    ]
    assert _pick_main_poi("新辰里购物中心", cands).name == "新辰里购物中心(亚运村店)"


def check_extract_hhmm():
    assert _extract_hhmm(">=11:00") == "11:00"
    assert _extract_hhmm("13:05前到") == "13:05"
    assert _extract_hhmm("~黄昏") is None
    assert _extract_hhmm(None) is None


async def check_resolve_named_gate():
    use_fake({
        "霍格沃茨魔法学校": [_poi("明月魔法学院", [116.69, 39.85], "体育休闲服务;休闲场所;休闲场所")],
        "鸟巢": [_poi("国家体育场", [116.39, 39.99], "体育休闲服务;运动场馆;综合体育馆")],
    })
    assert await sch._resolve_named("霍格沃茨魔法学校", "北京") == []           # gated
    landmark = await sch._resolve_named("鸟巢", "北京")
    assert landmark and landmark[0].name == "国家体育场"                       # alias kept


# --------------------------------------------------------------------------
# Group 2 — scheduling + feasibility (the routing-correctness fixes)
# --------------------------------------------------------------------------
def _biz_intent(meeting2_start: str) -> IntentObject:
    return IntentObject(
        constraints=Constraints(city="上海", start=Endpoint(type="current")),
        tasks=[Task(id="t1", type="dining", intent="午饭", dwell_min=40),
               Task(id="t2", type="leisure", intent="安静待着", dwell_min=30)],
        fixed_events=[FixedEvent(title="客户会议", place="上海中心", start="10:00", end="12:00"),
                      FixedEvent(title="项目会议", place="浦东软件园", start=meeting2_start)],
    )


async def check_fixed_events_scheduled():
    use_fake()
    plan = await build_plan(_biz_intent("14:00"), None, "上海")
    kinds = [s.kind for s in plan.timeline]
    names = [s.name for s in plan.timeline]
    assert kinds.count("fixed") == 2, kinds                 # both meetings present
    assert "上海中心 · 客户会议" in names and "浦东软件园 · 项目会议" in names
    assert plan.timeline[0].kind == "fixed"                 # anchored by meeting, no synthetic start
    assert plan.feasibility.ok                              # 2h gap is enough


async def check_feasibility_conflict():
    use_fake()
    # Second meeting only 10 min after the first ends, with 70 min of tasks: impossible.
    plan = await build_plan(_biz_intent("12:10"), None, "上海")
    assert not plan.feasibility.ok, plan.feasibility.note
    assert "赶不上" in plan.feasibility.note


async def check_optional_between_meetings_does_not_break_fixed_event():
    use_fake()
    intent = IntentObject(
        constraints=Constraints(city="上海", start=Endpoint(type="current")),
        tasks=[Task(id="t1", type="leisure", intent="安静待着", dwell_min=180)],
        fixed_events=[
            FixedEvent(title="客户会议", place="上海中心", start="10:00", end="12:00"),
            FixedEvent(title="项目会议", place="浦东软件园", start="14:00"),
        ],
    )
    plan = await build_plan(intent, None, "上海")
    assert not plan.feasibility.ok, plan.feasibility.note
    assert "赶不上" in plan.feasibility.note and "换一个" in plan.feasibility.note
    assert [s.kind for s in plan.timeline].count("fixed") == 2
    assert any(s.kind == "poi" for s in plan.timeline)


async def check_business_day_keeps_lunch_between_meetings_and_starts_at_hotel():
    use_fake()
    intent = IntentObject(
        constraints=Constraints(
            city="上海",
            start=Endpoint(type="named", value="上海锦江汤臣洲际大酒店"),
            end=Endpoint(type="named", value="上海锦江汤臣洲际大酒店"),
            time_window=TimeWindow(end="21:00"),
        ),
        explicit_pois=[
            ExplicitPOI(name="上海中心大厦", fixed_order_index=0),
            ExplicitPOI(name="上海瑞金洲际酒店", fixed_order_index=1),
            ExplicitPOI(name="上海锦江汤臣洲际大酒店", fixed_order_index=2),
        ],
        tasks=[
            Task(id="m1", type="meeting", intent="第一个会", at="上海中心大厦", dwell_min=120, needs_poi=True, explicit=True),
            Task(id="t1", type="dining", intent="吃饭", dwell_min=60, needs_poi=True),
            Task(id="m2", type="meeting", intent="另一个会", at="上海瑞金洲际酒店", dwell_min=60, needs_poi=True, explicit=True),
            Task(id="t2", type="leisure", intent="出去玩一下", dwell_min=70, needs_poi=True),
        ],
        fixed_events=[
            FixedEvent(title="第一个会", place="上海中心大厦", start="09:00", end="11:00"),
            FixedEvent(title="另一个会", place="上海瑞金洲际酒店", start="13:00"),
        ],
    )
    plan = await build_plan(intent, None, "上海")
    names = [s.name for s in plan.timeline]
    kinds = [s.kind for s in plan.timeline]
    assert plan.feasibility.ok, plan.feasibility.note
    assert "赶不上" not in plan.feasibility.note and "未加入" not in plan.feasibility.note
    assert kinds[0] == "start" and names[0] == "上海锦江汤臣洲际大酒店"
    assert plan.timeline[0].time <= "08:40"
    assert sum(1 for s in plan.timeline if "上海中心大厦" in s.name) == 1
    assert sum(1 for s in plan.timeline if "上海瑞金洲际酒店" in s.name) == 1
    first_meeting = next(i for i, s in enumerate(plan.timeline) if "第一个会" in s.name)
    second_meeting = next(i for i, s in enumerate(plan.timeline) if "另一个会" in s.name)
    lunch = next(i for i, s in enumerate(plan.timeline) if first_meeting < i < second_meeting and s.kind == "poi")
    play = next(i for i, s in enumerate(plan.timeline) if i > second_meeting and s.kind == "poi")
    end = next(i for i, s in enumerate(plan.timeline) if s.kind == "end")
    assert first_meeting < lunch < second_meeting < play < end
    assert plan.timeline[end].time == "21:00"


async def check_llm_path_is_augmented_with_return_endpoint_and_time():
    from app.agent.pipeline import _extract_intent_llm

    class FakeLLM:
        enabled = True

        async def complete_json(self, system, user, temp, stage="json"):
            return {
                "city": "上海",
                "start": {"place": None, "transport_hint": None},
                "end": {"place": None},
                "segments": [],
                "date": "tomorrow",
                "time_window": {},
                "fixed_events": [
                    {"title": "第一个会", "place": "上海中心大厦", "start": "09:00", "end": "11:00"},
                    {"title": "另一个会", "place": "上海瑞金洲际酒店", "start": "13:00", "end": None},
                ],
                "clarification_needed": [],
            }

    req = ChatRequest(
        message="明天到达上海锦江汤臣洲际大酒店，早上九点到上海中心大厦开会，下午一点在上海瑞金洲际酒店有另一个会，晚上九点回到上海锦江汤臣洲际大酒店。",
        city="上海",
    )
    intent = await _extract_intent_llm(req, FakeLLM())
    # Simulate the public _extract_intent post-processing path.
    from app.agent.heuristics import augment_intent
    intent = augment_intent(intent, req.message)
    assert intent.constraints.start.value == "上海锦江汤臣洲际大酒店"
    assert intent.constraints.end and intent.constraints.end.value == "上海锦江汤臣洲际大酒店"
    assert intent.constraints.time_window.end == "21:00"


async def check_time_hint_floor():
    use_fake()
    intent = IntentObject(
        constraints=Constraints(city="北京", start=Endpoint(type="current"),
                                time_window=TimeWindow(start="08:00")),
        tasks=[Task(id="t1", type="dining", intent="brunch", dwell_min=90,
                    needs_poi=True, time_hint=">=11:00")],
    )
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    poi = [s for s in plan.timeline if s.kind == "poi"][0]
    assert poi.time >= "11:00", f"brunch scheduled at {poi.time}, ignoring >=11:00 hint"


async def check_time_window_overrun():
    use_fake()
    intent = IntentObject(
        constraints=Constraints(city="北京", start=Endpoint(type="current"),
                                time_window=TimeWindow(start="20:00", end="21:00")),
        tasks=[Task(id=f"t{i}", type="sightseeing", intent=f"点{i}", dwell_min=120)
               for i in range(1, 4)],
    )
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    assert not plan.feasibility.ok            # 3x120min from 20:00 can't end by 21:00
    assert "晚" in plan.feasibility.note or "午夜" in plan.feasibility.note


# --------------------------------------------------------------------------
# Group 3 — pipeline robustness (the parsing/UX fixes), keyless path
# --------------------------------------------------------------------------
async def _stream_types(msg, scenario=None):
    use_fake()
    types, clar = [], None
    async for ev in plan_stream(ChatRequest(message=msg, scenario=scenario, city="北京")):
        types.append(ev.type)
        if ev.type == "clarify":
            clar = ev.text
    return types, clar


async def _stream_clarify(msg, scripted=None):
    use_fake(scripted)
    types, clar, options = [], None, []
    async for ev in plan_stream(ChatRequest(message=msg, city="北京")):
        types.append(ev.type)
        if ev.type == "clarify":
            clar = ev.text
            options = ev.options
    return types, clar, options


async def _stream_clarify_req(req: ChatRequest, scripted=None):
    use_fake(scripted)
    types, clar, options, intent = [], None, [], None
    async for ev in plan_stream(req):
        types.append(ev.type)
        if ev.type == "clarify":
            clar = ev.text
            options = ev.options
            intent = ev.intent
    return types, clar, options, intent


async def check_empty_and_noise_clarify():
    for msg in ("", "   \n\t", "1+1等于几", "😀🍜🌆"):
        types, _ = await _stream_types(msg)
        assert "clarify" in types and "plan" not in types, f"{msg!r} -> {types}"


async def check_valid_trip_plans():
    types, _ = await _stream_types("带我去三里屯")          # place recovered -> plannable
    assert "plan" in types, types


async def check_preset_chip_still_replays():
    types, _ = await _stream_types("（点了示例卡）", scenario="sc1")
    assert "plan" in types, types


async def check_free_text_not_hijacked_by_demo():
    # Free text with demo keywords ("安静"+"望京") must NOT replay the canned sc1.
    plan = None
    use_fake()
    async for ev in plan_stream(ChatRequest(message="今天压力好大，想去望京找个安静的地方", city="北京")):
        if ev.type == "plan":
            plan = ev.plan
    if plan:  # may instead clarify (mood) — either way it must not be the canned plan
        assert not any("Voyage" in s.name or "郎园" in s.name for s in plan.timeline)


def check_plannable_helpers():
    assert not _is_plannable(IntentObject())
    assert _is_plannable(IntentObject(tasks=[Task(id="t1", type="dining")]))
    assert _is_plannable(IntentObject(explicit_pois=[ExplicitPOI(name="三里屯")]))
    contradiction = IntentObject(clarification_needed=["您要求特别热闹但又绝对安静，这两者矛盾，更想要哪种？"])
    assert "矛盾" in _not_plannable_question(contradiction)   # surfaces the model's question
    assert _not_plannable_question(IntentObject(clarification_needed=["budget_total"]))  # filters bare field names


# --------------------------------------------------------------------------
# Group 4 — judge round 2 fixes
# --------------------------------------------------------------------------
def check_dwell_clamp():
    assert Task(id="t", dwell_min=100000).dwell_min == 360
    assert Task(id="t", dwell_min=-5).dwell_min == 0
    assert Task(id="t", dwell_min="abc").dwell_min == 60  # type: ignore[arg-type]


def check_fixed_events_are_explicit():
    only_meetings = IntentObject(fixed_events=[FixedEvent(title="会", place="上海中心", start="10:00")])
    assert only_meetings.is_explicit                       # not the mood card
    assert build_understanding(only_meetings).kind == "explicit"


def check_date_and_anchor_helpers():
    assert _date_offset("明天") == 1 and _date_offset("后天") == 2 and _date_offset("today") == 0
    assert _date_offset("2099-01-01") > 0
    it = IntentObject(constraints=Constraints(time_window=TimeWindow(start="09:30")),
                      tasks=[Task(id="t1", type="dining", time_hint=">=14:00"),
                             Task(id="t2", type="leisure", time_hint="~黄昏")])
    assert _earliest_anchor(it) == "09:30"                 # min across window + hints


async def check_placeholder_only_not_feasible():
    use_fake({"霍格沃茨魔法学校": [_poi("明月魔法学院", [116.69, 39.85], "体育休闲服务;休闲场所;休闲场所")]})
    intent = IntentObject(
        constraints=Constraints(city="北京", start=Endpoint(type="current")),
        explicit_pois=[ExplicitPOI(name="霍格沃茨魔法学校", fixed_order_index=0)],
        tasks=[Task(id="t1", type="leisure", at="霍格沃茨魔法学校", explicit=True)],
    )
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    assert not plan.feasibility.ok and "没找到" in plan.feasibility.note
    assert "已按真实地点" not in plan.feasibility.note      # don't claim what isn't there


async def check_cross_city_guard():
    use_fake({"外滩": [_poi("外滩", [121.490, 31.240])]})   # 上海, ~1000km from 北京 start
    intent = IntentObject(
        constraints=Constraints(city="北京", start=Endpoint(type="current")),
        explicit_pois=[ExplicitPOI(name="外滩", fixed_order_index=0)],
        tasks=[Task(id="t1", type="sightseeing", at="外滩", explicit=True)],
    )
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    assert not plan.feasibility.ok and "跨城市" in plan.feasibility.note


async def check_no_open_hours_overclaim():
    use_fake()
    intent = IntentObject(constraints=Constraints(city="北京", start=Endpoint(type="current")),
                          tasks=[Task(id="t1", type="leisure", intent="逛", at="三里屯", explicit=True)],
                          explicit_pois=[ExplicitPOI(name="三里屯", fixed_order_index=0)])
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    assert "营业时间" not in plan.feasibility.note          # we don't verify it, so don't say it


def check_merge_patch_protects_itinerary():
    prior = IntentObject(
        constraints=Constraints(start=Endpoint(type="named", value="国贸"),
                                end=Endpoint(type="named", value="望京")),
        explicit_pois=[ExplicitPOI(name="三里屯", fixed_order_index=0),
                       ExplicitPOI(name="后海", fixed_order_index=1)],
        tasks=[Task(id="t1", type="leisure", at="三里屯", needs_poi=True, explicit=True),
               Task(id="t2", type="sightseeing", at="后海", needs_poi=True, explicit=True)],
    )
    # A *change* that the model collapsed to one stop + lost start/end:
    collapsed = IntentObject(
        constraints=Constraints(start=Endpoint(type="current")),
        explicit_pois=[ExplicitPOI(name="静安营业厅", fixed_order_index=0)],
        tasks=[Task(id="t1", type="other", at="静安营业厅", needs_poi=True)],
    )
    merged = _merge_patch(prior, collapsed, "三里屯那个太远了，换个近一点的。")
    assert _anchor_count(merged) >= 2                      # itinerary not destroyed
    assert merged.constraints.start.value == "国贸"        # start preserved
    # A *removal* is allowed to shrink, but still inherits start/end:
    removed = IntentObject(
        constraints=Constraints(start=Endpoint(type="current")),
        explicit_pois=[ExplicitPOI(name="三里屯", fixed_order_index=0)],
        tasks=[Task(id="t1", type="leisure", at="三里屯", needs_poi=True)],
    )
    merged2 = _merge_patch(prior, removed, "后海就不去了，去掉吧。")
    assert merged2.constraints.start.value == "国贸" and merged2.constraints.end.value == "望京"
    assert _anchor_count(merged2) == 1                     # shrink honoured


def check_merge_patch_rejects_full_rewrite():
    # The live failure: "换三里屯" -> model rewrote BOTH stops into 3× the same
    # 国贸 restaurant. anchor_count is 3 (not a collapse), but it shares no place
    # with the prior, so it's a rewrite, not an edit -> keep the prior plan.
    prior = IntentObject(
        explicit_pois=[ExplicitPOI(name="三里屯", fixed_order_index=0),
                       ExplicitPOI(name="后海", fixed_order_index=1)],
        tasks=[Task(id="t1", type="leisure", at="三里屯", needs_poi=True, explicit=True),
               Task(id="t2", type="sightseeing", at="后海", needs_poi=True, explicit=True)],
    )
    rewrite = IntentObject(
        explicit_pois=[ExplicitPOI(name="一个叫川的地方", fixed_order_index=i) for i in range(3)],
        tasks=[Task(id=f"t{i}", type="dining", at="一个叫川的地方", needs_poi=True) for i in range(3)],
    )
    merged = _merge_patch(prior, rewrite, "三里屯那个咖啡馆太远了，换个国贸附近近一点的。")
    assert {p.name for p in merged.explicit_pois} == {"三里屯", "后海"}   # prior kept


async def check_dedup_consecutive_pois():
    use_fake({"川菜": [_poi("一个叫川的地方", [116.460, 39.910], "餐饮服务;中餐厅;中餐厅")]})
    intent = IntentObject(
        constraints=Constraints(city="北京", start=Endpoint(type="current")),
        tasks=[Task(id=f"t{i}", type="dining", intent="川菜", needs_poi=True) for i in range(3)],
    )
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    poi_stops = [s for s in plan.timeline if s.kind == "poi"]
    assert len(poi_stops) == 1, [s.name for s in poi_stops]   # 3 identical -> 1


async def check_patch_merge_covers_fallback_path():
    # The merge guard must wrap BOTH the LLM and the heuristic path, because the
    # heuristic is exactly what runs when an LLM patch times out (the live bug).
    # Keyless here -> heuristic + merge in _extract_intent.
    prior = IntentObject(
        constraints=Constraints(start=Endpoint(type="named", value="国贸"),
                                end=Endpoint(type="named", value="望京")),
        explicit_pois=[ExplicitPOI(name="三里屯", fixed_order_index=0),
                       ExplicitPOI(name="后海", fixed_order_index=1)],
        tasks=[Task(id="t1", type="leisure", at="三里屯", needs_poi=True, explicit=True),
               Task(id="t2", type="sightseeing", at="后海", needs_poi=True, explicit=True)],
    )
    req = ChatRequest(message="三里屯那个太远了，换个近一点的", city="北京", intent=prior)
    intent = await _extract_intent(req)
    assert _anchor_count(intent) >= 2 and intent.constraints.start.value == "国贸"


async def check_alternatives_and_swap_recompute():
    A = _poi("咖啡A", [116.41, 39.91], "餐饮服务;咖啡厅;咖啡厅", rating=4.8)
    B = _poi("咖啡B", [116.42, 39.92], "餐饮服务;咖啡厅;咖啡厅", rating=4.5)
    C = _poi("咖啡C", [116.43, 39.93], "餐饮服务;咖啡厅;咖啡厅", rating=4.2)
    use_fake({"咖啡馆": [A, B, C]})
    intent = IntentObject(
        constraints=Constraints(city="北京", start=Endpoint(type="current"),
                                end=Endpoint(type="named", value="望京")),
        tasks=[Task(id="t1", type="leisure", intent="", needs_poi=True, dwell_min=60)],
    )
    plan = await build_plan(intent, [116.40, 39.90], "北京")
    poi = next(s for s in plan.timeline if s.kind == "poi")
    assert poi.name == "咖啡A"                                  # best-rated is the pick
    assert [a.name for a in poi.alternatives] == ["咖啡B", "咖啡C"]  # the rest are alternatives
    assert poi.dwell_min == 60

    # User swaps to 咖啡B; client replaces name/location, server re-routes.
    idx = plan.timeline.index(poi)
    alt = poi.alternatives[0]
    plan.timeline[idx].name, plan.timeline[idx].location, plan.timeline[idx].rating = (
        alt.name, alt.location, alt.rating,
    )
    plan2 = await recompute_plan(plan.timeline, "北京", plan.intent)
    swapped = next(s for s in plan2.timeline if s.kind == "poi")
    assert swapped.name == "咖啡B" and swapped.location == [116.42, 39.92]
    assert swapped.time and swapped.leg                         # times + legs recomputed
    assert any(s.kind == "end" for s in plan2.timeline)         # rest of the route preserved


async def check_dining_clarify_not_over_triggering():
    # Rich, clear itinerary with a dining task -> plan, don't interrogate.
    types, _ = await _stream_types("先去三里屯逛逛，再去南锣鼓巷吃饭，最后去后海散步")
    assert "plan" in types, types
    # Light, dining-led asks are now advisory validation issues: don't block
    # the user; let the planner pick a sensible default.
    types2, _ = await _stream_types("随便找个地方吃饭")
    assert "plan" in types2 and "clarify" not in types2, types2


async def check_ambiguous_home_end_clarifies():
    # "望京的家" is an area + private referent, not a navigable endpoint.
    types, clar = await _stream_types("下午先去南锣鼓巷逛逛，然后吃饭，晚上回望京的家")
    assert "clarify" in types and "plan" not in types, types
    assert clar and "望京的家" in clar and "具体终点" in clar

    # A bare area after "return" is also not enough for navigation.
    types_area, clar_area = await _stream_types("下午先去南锣鼓巷逛逛，晚上回望京")
    assert "clarify" in types_area and "plan" not in types_area, types_area
    assert clar_area and "望京" in clar_area and "具体终点" in clar_area

    # Generic return commands should ask where "home/back" actually is.
    types2, clar2 = await _stream_types("下午逛逛公园，晚上回去")
    assert "clarify" in types2 and "plan" not in types2, types2
    assert clar2 and "回去" in clar2 and "具体终点" in clar2


async def check_ambiguous_end_offers_origin_guess():
    req = ChatRequest(
        message="下午先去南锣鼓巷逛逛，晚上回去",
        city="北京",
        origin=GeoPoint(lng=116.40, lat=39.90, label="当前位置A"),
        origin_status="available",
    )
    types, clar, options, _ = await _stream_clarify_req(req)
    assert "clarify" in types and "plan" not in types, types
    assert clar and "下面的猜测" in clar
    assert options and options[0].label == "回到当前位置"
    assert "终点是 当前位置A" in options[0].message


async def check_current_start_unknown_clarifies():
    req = ChatRequest(
        message="从这里出发，去三里屯逛逛",
        city="北京",
        origin=None,
        origin_status="denied",
    )
    types, clar, options, _ = await _stream_clarify_req(req)
    assert "clarify" in types and "plan" not in types, types
    assert clar and "拿不到你的当前位置" in clar
    assert options and options[0].label == "从 三里屯"


async def check_clarify_answer_completes_prior_intent():
    first = ChatRequest(
        message="下午先去南锣鼓巷逛逛，晚上回去",
        city="北京",
        origin=GeoPoint(lng=116.40, lat=39.90, label="当前位置A"),
        origin_status="available",
    )
    types, _, options, pending = await _stream_clarify_req(first)
    assert "clarify" in types and pending and pending.is_available is False

    use_fake()
    planned = False
    answer = ChatRequest(
        message=options[0].message,
        city="北京",
        origin=GeoPoint(lng=116.40, lat=39.90, label="当前位置A"),
        origin_status="available",
        intent=pending,
    )
    async for ev in plan_stream(answer):
        if ev.type == "plan":
            planned = True
            assert ev.plan.intent and ev.plan.intent.is_available is True
    assert planned


async def check_p1_clarify_accepts_internal_intent_schema():
    prior = IntentObject(
        is_available=False,
        pending_question_type="end",
        pending_field="constraints.end",
        constraints=Constraints(city="北京", start=Endpoint(type="current")),
        explicit_pois=[ExplicitPOI(name="南锣鼓巷", fixed_order_index=0)],
        tasks=[Task(id="t1", type="sightseeing", intent="逛逛", at="南锣鼓巷", explicit=True)],
        clarification_needed=["请确认终点"],
    )

    class FakeLLM:
        async def complete_json(self, system, user, temp, stage="json"):
            data = prior.model_dump()
            data["is_available"] = True
            data["pending_question_type"] = None
            data["pending_field"] = None
            data["constraints"]["end"] = {"type": "named", "value": "当前位置A", "source": "nl_extract", "location": None}
            data["clarification_needed"] = []
            return data

    req = ChatRequest(message="回到当前位置A", city="北京", intent=prior)
    intent = await _complete_pending_intent_llm(req, prior, FakeLLM())
    assert intent.is_available is True
    assert intent.constraints.end and intent.constraints.end.value == "当前位置A"
    assert intent.explicit_pois and intent.explicit_pois[0].name == "南锣鼓巷"
    assert intent.tasks and intent.tasks[0].at == "南锣鼓巷"


def check_origin_label_preserves_full_start_address():
    req = ChatRequest(
        message="从当前位置出发，去五道口",
        city="北京",
        origin=GeoPoint(
            lng=116.326,
            lat=40.006,
            label="北京市海淀区清华园清华大学清华大学附属中学",
        ),
        origin_status="available",
    )
    intent = IntentObject(
        constraints=Constraints(
            city="北京",
            start=Endpoint(type="named", value="清华大学附属中学", source="nl_extract"),
        )
    )
    updated = _apply_origin_to_start(req, intent)
    assert updated.constraints.start.value == "北京市海淀区清华园清华大学清华大学附属中学"
    assert updated.constraints.start.source == "geolocation"
    assert updated.constraints.start.location == [116.326, 40.006]


async def check_named_hotel_end_does_not_clarify():
    # A full hotel POI name is navigable; don't confuse it with "the hotel in Wangjing".
    types, _ = await _stream_types("下午先去南锣鼓巷逛逛，晚上回北京望京凯悦酒店")
    assert "plan" in types and "clarify" not in types, types


async def check_school_endpoint_choice_clarify():
    scripted = {
        "中国科学院大学": [
            _poi("中国科学院大学(雁栖湖校区)", [116.680, 40.410], "科教文化服务;学校;高等院校", address="北京市怀柔区怀北镇"),
            _poi("中国科学院大学(玉泉路校区)", [116.250, 39.910], "科教文化服务;学校;高等院校", address="北京市石景山区玉泉路"),
        ]
    }
    types, clar, options = await _stream_clarify("下午先去南锣鼓巷逛逛，晚上回中国科学院大学", scripted)
    assert "clarify" in types and "plan" not in types, types
    assert clar and "中国科学院大学" in clar and "终点" in clar
    assert len(options) == 2
    assert "雁栖湖校区" in options[0].label and "怀柔" in options[0].description

    # Clicking a concrete campus should continue planning, not ask the same question again.
    types2, _, _ = await _stream_clarify(options[0].message, scripted)
    assert "plan" in types2 and "clarify" not in types2, types2


async def check_school_start_choice_clarify():
    scripted = {
        "中国科学院大学": [
            _poi("中国科学院大学(雁栖湖校区)", [116.680, 40.410], "科教文化服务;学校;高等院校", address="北京市怀柔区怀北镇"),
            _poi("中国科学院大学(玉泉路校区)", [116.250, 39.910], "科教文化服务;学校;高等院校", address="北京市石景山区玉泉路"),
        ]
    }
    types, clar, options = await _stream_clarify("从中国科学院大学出发，去三里屯逛逛", scripted)
    assert "clarify" in types and "plan" not in types, types
    assert clar and "中国科学院大学" in clar and "起点" in clar
    assert len(options) == 2


async def check_geolocated_school_start_does_not_clarify():
    scripted = {
        "北京市海淀区清华园清华大学清华大学附属中学": [
            _poi("清华大学附属中学", [116.326, 40.006], "科教文化服务;学校;中学", address="北京市海淀区清华园"),
            _poi("清华大学附属中学将台路校区", [116.490, 39.970], "科教文化服务;学校;中学", address="北京市朝阳区将台路"),
        ]
    }
    req = ChatRequest(
        message="从当前位置出发，去三里屯逛逛",
        city="北京",
        origin=GeoPoint(
            lng=116.326,
            lat=40.006,
            label="北京市海淀区清华园清华大学清华大学附属中学",
        ),
        origin_status="available",
    )
    use_fake(scripted)
    types = []
    async for ev in plan_stream(req):
        types.append(ev.type)
    assert "plan" in types and "clarify" not in types, types


async def check_place_resolution_fills_vague_task_before_planning():
    use_fake({
        "咖啡馆": [
            _poi("静静咖啡", [116.401, 39.900], "餐饮服务;咖啡厅", rating=4.8, address="安静路1号"),
            _poi("热闹咖啡", [116.402, 39.900], "餐饮服务;咖啡厅", rating=4.1),
        ]
    })
    intent = IntentObject(
        constraints=Constraints(
            start=Endpoint(type="current", value="当前位置", location=[116.40, 39.90], source="geolocation")
        ),
        tasks=[Task(id="t1", type="leisure", intent="咖啡馆", dwell_min=45, explicit=False)],
    )
    resolution = await resolve_place_slots(intent, [116.40, 39.90], "北京")
    assert resolution.status == "places_ready"
    assert intent.tasks[0].at == "静静咖啡"
    assert intent.tasks[0].location == [116.401, 39.9]

    plan = await build_plan(intent, [116.40, 39.90], "北京")
    assert any(s.name == "静静咖啡" for s in plan.timeline)


async def check_place_resolution_preserves_precise_current_start():
    use_fake()
    full = "北京市海淀区清华园清华大学清华大学附属中学"
    intent = IntentObject(
        constraints=Constraints(
            start=Endpoint(type="current", value=full, location=[116.326, 40.004], source="geolocation")
        ),
        tasks=[Task(id="t1", type="dining", intent="午饭", dwell_min=50)],
    )
    resolution = await resolve_place_slots(intent, [116.326, 40.004], "北京")
    start = resolution.slots[0]
    assert start.role == "start"
    assert start.selected and start.selected.name == full
    assert intent.constraints.start.value == full
    assert intent.constraints.start.location == [116.326, 40.004]


async def check_dining_search_uses_previous_and_next_anchors():
    class TrackingAMap(FakeAMap):
        def __init__(self):
            super().__init__()
            self.around_calls = []

        async def search_poi_text(self, keywords, region=None, types=None, page_size=10):
            if keywords == "五道口":
                return [_poi("五道口", [116.3373, 39.9929])]
            return await super().search_poi_text(keywords, region, types, page_size)

        async def search_poi_around(self, keywords, location, radius_m=3000, types=None,
                                    sortrule="weight", page_size=10):
            self.around_calls.append((keywords, list(location), radius_m, types))
            return [_poi(f"{keywords}@{location[0]:.4f}", [location[0] + 0.001, location[1]], rating=4.6)]

    fake = TrackingAMap()
    sch.get_amap = lambda: fake
    intent = IntentObject(
        constraints=Constraints(start=Endpoint(type="current", value="当前位置", location=[116.3200, 40.0000], source="geolocation")),
        explicit_pois=[ExplicitPOI(name="五道口", fixed_order_index=0)],
        tasks=[
            Task(id="t1", type="dining", intent="吃饭", dwell_min=60, explicit=True),
            Task(id="t2", type="leisure", intent="逛逛", dwell_min=60, explicit=True),
        ],
    )
    await resolve_place_slots(intent, [116.3200, 40.0000], "北京")
    dining_calls = [call for call in fake.around_calls if "餐厅" in call[0]]
    assert any(call[1] == [116.3200, 40.0000] for call in dining_calls), dining_calls
    assert any(call[1] == [116.3373, 39.9929] for call in dining_calls), dining_calls
    assert all(call[3] == "050000" for call in dining_calls), dining_calls


async def check_dining_inside_named_area_uses_area_around_search_first():
    class TrackingAMap(FakeAMap):
        def __init__(self):
            super().__init__()
            self.text_calls = []
            self.around_calls = []

        async def search_poi_text(self, keywords, region=None, types=None, page_size=10):
            self.text_calls.append(keywords)
            if keywords == "五道口":
                return [_poi("五道口", [116.3373, 39.9929])]
            return [_poi(keywords, _hash_loc(keywords))]

        async def search_poi_around(self, keywords, location, radius_m=3000, types=None,
                                    sortrule="weight", page_size=10):
            self.around_calls.append((keywords, list(location), radius_m, types))
            return [_poi("五道口附近餐厅", [location[0] + 0.001, location[1]], rating=4.7)]

    fake = TrackingAMap()
    sch.get_amap = lambda: fake
    intent = IntentObject(
        constraints=Constraints(start=Endpoint(type="current", location=[116.3200, 40.0000], source="geolocation")),
        explicit_pois=[ExplicitPOI(name="五道口", fixed_order_index=0)],
        tasks=[Task(id="t1", type="dining", intent="吃饭", at="五道口", dwell_min=60, explicit=True)],
    )
    await resolve_place_slots(intent, [116.3200, 40.0000], "北京")
    assert ("餐厅|饭店|中餐|快餐", [116.3373, 39.9929], 3000, "050000") in fake.around_calls, fake.around_calls
    assert "五道口 餐厅" not in fake.text_calls, fake.text_calls


async def check_dining_between_fixed_events_uses_meeting_anchors():
    class TrackingAMap(FakeAMap):
        def __init__(self):
            super().__init__()
            self.around_calls = []

        async def search_poi_around(self, keywords, location, radius_m=3000, types=None,
                                    sortrule="weight", page_size=10):
            self.around_calls.append((keywords, list(location), radius_m, types))
            return [_poi(f"{keywords}@{location[0]:.3f}", [location[0] + 0.001, location[1]], rating=4.6)]

    fake = TrackingAMap()
    sch.get_amap = lambda: fake
    intent = IntentObject(
        constraints=Constraints(start=Endpoint(type="current", location=[116.10, 39.90], source="geolocation")),
        fixed_events=[
            FixedEvent(title="上午会", place="会议点A", start="10:00", end="11:30", location=[116.30, 39.90]),
            FixedEvent(title="下午会", place="会议点B", start="14:00", end="15:00", location=[116.50, 39.90]),
        ],
        tasks=[Task(id="t1", type="dining", intent="午饭", dwell_min=60, time_hint="12:00")],
    )
    await resolve_place_slots(intent, [116.10, 39.90], "北京")
    dining_calls = [call for call in fake.around_calls if "餐厅" in call[0]]
    assert any(call[1] == [116.30, 39.90] for call in dining_calls), dining_calls
    assert any(call[1] == [116.50, 39.90] for call in dining_calls), dining_calls
    assert not any(call[1] == [116.10, 39.90] for call in dining_calls), dining_calls
    assert all(call[3] == "050000" for call in dining_calls), dining_calls


async def check_lunch_search_is_normalized_before_amap():
    class TrackingAMap(FakeAMap):
        def __init__(self):
            super().__init__()
            self.around_calls = []

        async def search_poi_around(self, keywords, location, radius_m=3000, types=None,
                                    sortrule="weight", page_size=10):
            self.around_calls.append((keywords, list(location), radius_m, types))
            if keywords == "午饭":
                return []
            if keywords == "餐厅|饭店|中餐|快餐" and types == "050000":
                return [_poi("路线附近餐厅", [location[0] + 0.001, location[1]], rating=4.6)]
            return []

    fake = TrackingAMap()
    sch.get_amap = lambda: fake
    intent = IntentObject(
        constraints=Constraints(start=Endpoint(type="current", location=[116.192639, 40.244939], source="geolocation")),
        tasks=[Task(id="t1", type="dining", intent="午饭", dwell_min=60)],
    )
    resolution = await resolve_place_slots(intent, [116.192639, 40.244939], "北京")
    assert resolution.status == "places_ready"
    assert intent.tasks[0].at == "路线附近餐厅"
    assert all(call[0] != "午饭" for call in fake.around_calls), fake.around_calls
    assert ("餐厅|饭店|中餐|快餐", [116.192639, 40.244939], 3000, "050000") in fake.around_calls


async def check_dining_does_not_use_end_before_future_vague_task():
    class TrackingAMap(FakeAMap):
        def __init__(self):
            super().__init__()
            self.around_calls = []

        async def search_poi_around(self, keywords, location, radius_m=3000, types=None,
                                    sortrule="weight", page_size=10):
            self.around_calls.append((keywords, list(location), radius_m, types))
            return [_poi(f"{keywords}@{location[0]:.3f}", [location[0] + 0.001, location[1]], rating=4.6)]

    fake = TrackingAMap()
    sch.get_amap = lambda: fake
    intent = IntentObject(
        constraints=Constraints(
            start=Endpoint(type="current", location=[116.10, 39.90], source="geolocation"),
            end=Endpoint(type="named", value="终点", location=[116.80, 39.90], source="nl_extract"),
        ),
        tasks=[
            Task(id="t1", type="dining", intent="吃饭", dwell_min=60),
            Task(id="t2", type="leisure", intent="找个地方休息", dwell_min=60),
        ],
    )
    await resolve_place_slots(intent, [116.10, 39.90], "北京")
    dining_calls = [call for call in fake.around_calls if "餐厅" in call[0]]
    assert any(call[1] == [116.10, 39.90] for call in dining_calls), dining_calls
    assert not any(call[1] == [116.80, 39.90] for call in dining_calls), dining_calls
    assert all(call[3] == "050000" for call in dining_calls), dining_calls


def check_fuzzy_preference_clarify_is_nonblocking():
    intent = IntentObject(
        is_available=False,
        implicit_preferences=ImplicitPreferences(mood="有点累", vibe_tags=["安静"]),
        tasks=[
            Task(id="t1", type="leisure", intent="安静的地方坐坐", dwell_min=70),
            Task(id="t2", type="dining", intent="吃点东西", dwell_min=60),
        ],
        clarification_needed=["你想去哪个具体的地方？比如附近的咖啡馆、茶馆或餐厅。"],
    )
    updated = _allow_nonblocking_llm_clarification(intent)
    assert updated.is_available
    assert updated.clarification_needed == []

    blocking = IntentObject(
        is_available=False,
        tasks=[Task(id="t1", type="leisure", intent="逛逛", dwell_min=60)],
        clarification_needed=["你说从当前位置出发，但定位不可用。请提供具体起点。"],
    )
    still_blocking = _allow_nonblocking_llm_clarification(blocking)
    assert not still_blocking.is_available
    assert still_blocking.clarification_needed


def check_file_parser_csv_itinerary():
    ctx = parse_attachment(
        "行程表.csv",
        "时间,地点,事项\n09:00,上海中心,开会\n12:00,附近,午饭\n".encode("utf-8"),
        "text/csv",
    )
    assert ctx.kind == "table"
    assert ctx.rows[0][:3] == ["时间", "地点", "事项"]
    assert "上海中心" in ctx.text
    assert "解析出" in ctx.summary


def check_file_parser_docx_text():
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>10:00 到上海中心开会</w:t></w:r></w:p>
    <w:p><w:r><w:t>14:00 去浦东软件园</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    ctx = parse_attachment("会议安排.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert ctx.kind == "document"
    assert "上海中心" in ctx.text
    assert "浦东软件园" in ctx.text


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
CHECKS = [
    check_base_name, check_name_related, check_landmark_and_facility,
    check_pick_main_poi_branch, check_extract_hhmm, check_resolve_named_gate,
    check_fixed_events_scheduled, check_feasibility_conflict, check_time_hint_floor,
    check_optional_between_meetings_does_not_break_fixed_event,
    check_business_day_keeps_lunch_between_meetings_and_starts_at_hotel,
    check_llm_path_is_augmented_with_return_endpoint_and_time, check_time_window_overrun,
    check_empty_and_noise_clarify, check_valid_trip_plans,
    check_preset_chip_still_replays, check_free_text_not_hijacked_by_demo,
    check_plannable_helpers,
    # judge round 2
    check_dwell_clamp, check_fixed_events_are_explicit, check_date_and_anchor_helpers,
    check_placeholder_only_not_feasible, check_cross_city_guard, check_no_open_hours_overclaim,
    check_merge_patch_protects_itinerary, check_merge_patch_rejects_full_rewrite,
    check_dedup_consecutive_pois, check_patch_merge_covers_fallback_path,
    check_alternatives_and_swap_recompute, check_dining_clarify_not_over_triggering,
    check_ambiguous_home_end_clarifies, check_named_hotel_end_does_not_clarify,
    check_ambiguous_end_offers_origin_guess, check_current_start_unknown_clarifies,
    check_clarify_answer_completes_prior_intent, check_p1_clarify_accepts_internal_intent_schema,
    check_origin_label_preserves_full_start_address,
    check_school_endpoint_choice_clarify, check_school_start_choice_clarify,
    check_geolocated_school_start_does_not_clarify,
    check_place_resolution_fills_vague_task_before_planning,
    check_place_resolution_preserves_precise_current_start,
    check_dining_search_uses_previous_and_next_anchors,
    check_dining_inside_named_area_uses_area_around_search_first,
    check_dining_between_fixed_events_uses_meeting_anchors,
    check_lunch_search_is_normalized_before_amap,
    check_dining_does_not_use_end_before_future_vague_task,
    check_fuzzy_preference_clarify_is_nonblocking,
    check_file_parser_csv_itinerary,
    check_file_parser_docx_text,
]


def main():
    passed = failed = 0
    for fn in CHECKS:
        try:
            asyncio.run(fn()) if asyncio.iscoroutinefunction(fn) else fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:
            import traceback
            print(f"  FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
