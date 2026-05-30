import { useEffect, useRef, useState } from "react";
import TopBar from "./components/TopBar";
import ChatPanel, { type AttachedFile } from "./components/ChatPanel";
import PlanPanel from "./components/PlanPanel";
import { getConfig, parseAttachment, recomputeRoute, reverseGeocode, streamChat, type PlaceCandidate } from "./api";
import type {
  AppConfig,
  ChatItem,
  ChatMessage,
  ChatRequestBody,
  PlaceCandidate as ResolvedPlaceCandidate,
  Plan,
  POIChoice,
  StreamEvent,
} from "./types";

type OriginStatus = NonNullable<ChatRequestBody["origin_status"]>;

interface Preset {
  id: string;
  label: string;
  city: string;
  text: string;
}

// The four mockup scripts. Clicking a chip sends `scenario=id`, which the
// backend short-circuits to the polished canned plan (stable demo, NFR-5).
const PRESETS: Preset[] = [
  {
    id: "sc1",
    label: "😮‍💨 模糊 · 情绪型",
    city: "北京",
    text: "今天有点累，想找个安静的地方待着，顺便吃个不赶时间的 brunch，傍晚想看看城市的样子，晚上回望京的家。",
  },
  {
    id: "sc2",
    label: "🗺 具体 · 清晰型",
    city: "北京",
    text: "下午先去南锣鼓巷逛逛，然后在附近找家评分高的烤鸭店吃晚饭，最后去景山公园看日落俯瞰故宫，晚上回望京的家。",
  },
  {
    id: "sc3",
    label: "🌏 泛化 · 其它城市",
    city: "成都",
    text: "周末想悠闲一点，找个有成都味儿的地方喝喝茶、吃点小吃，别太累，晚上看看夜景。",
  },
  {
    id: "sc4",
    label: "🗓 复杂 · 出差/开会",
    city: "上海",
    text: "明天上午 10 点到上海中心开会，开到 12 点；下午 2 点在浦东软件园还有个会。中间帮我安排午饭，再找个能安静待半小时的地方，两个会之间别太赶。",
  },
];

function itemsToHistory(items: ChatItem[]): ChatMessage[] {
  return items.flatMap((it): ChatMessage[] => {
    if (it.kind === "msg") return [{ role: it.role, content: it.content }];
    if (it.kind === "clarify") return [{ role: "assistant", content: it.text }];
    return [];
  });
}

export default function App() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [thinking, setThinking] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [city, setCity] = useState("北京");
  const [locationLabel, setLocationLabel] = useState("点击获取位置");
  const [originStatus, setOriginStatus] = useState<OriginStatus>("unknown");
  const [locating, setLocating] = useState(false);
  const [swappingIndex, setSwappingIndex] = useState<number | null>(null);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [activeScenario, setActiveScenario] = useState<string | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [originSearchFocusSignal, setOriginSearchFocusSignal] = useState(0);
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);

  const idCounter = useRef(0);
  const streamingIdRef = useRef<string | null>(null);
  const planRef = useRef<Plan | null>(null);
  const intentRef = useRef<unknown | null>(null);
  const itemsRef = useRef<ChatItem[]>([]);
  const originRef = useRef<{ lng: number; lat: number; label?: string } | null>(null);

  const uid = () => String(++idCounter.current);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    getConfig().then((cfg) => {
      if (cfg) {
        setConfig(cfg);
        setCity(cfg.default_city);
      }
    });
  }, []);

  useEffect(() => {
    requestLocation(false);
  }, []);

  const requestLocation = (manual = true) => {
    if (!navigator.geolocation) {
      setLocationLabel("浏览器不支持定位");
      setOriginStatus("unsupported");
      if (manual) setThinking("当前浏览器不支持定位，可以在对话里说“从 XX 出发”。");
      return;
    }

    setLocating(true);
    setOriginStatus("unknown");
    if (manual) setThinking("正在请求浏览器定位权限…");
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lng = pos.coords.longitude;
        const lat = pos.coords.latitude;
        const fallback = `当前位置 ${lat.toFixed(4)}, ${lng.toFixed(4)}`;
        const address = await reverseGeocode(lng, lat);
        const origin = {
          lng,
          lat,
          label: address ?? fallback,
        };
        originRef.current = origin;
        setOriginStatus("available");
        setLocationLabel(origin.label);
        setLocating(false);
        if (manual) setThinking(null);
      },
      (err) => {
        const reason =
          err.code === err.PERMISSION_DENIED
            ? "定位权限被拒绝"
            : err.code === err.POSITION_UNAVAILABLE
              ? "位置不可用"
              : "定位超时";
        const status: OriginStatus =
          err.code === err.PERMISSION_DENIED
            ? "denied"
            : err.code === err.POSITION_UNAVAILABLE
              ? "unavailable"
              : "timeout";
        originRef.current = null;
        setOriginStatus(status);
        setLocationLabel(reason);
        setLocating(false);
        if (manual) setThinking(`${reason}。你仍然可以在对话里说“从 清华大学 出发”。`);
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60_000 },
    );
  };

  // The user picked a start from the candidate dropdown — commit it as origin.
  const pickOrigin = (c: PlaceCandidate) => {
    originRef.current = { lng: c.lng, lat: c.lat, label: c.label };
    setOriginStatus("available");
    setLocationLabel(c.label);
    setThinking(null);
  };

  const onEvent = (ev: StreamEvent) => {
    switch (ev.type) {
      case "thinking":
        setThinking(ev.text);
        break;
      case "understanding":
        setItems((prev) => [...prev, { id: uid(), kind: "understanding", data: ev.understanding }]);
        break;
      case "plan":
        planRef.current = ev.plan;
        intentRef.current = ev.plan.intent ?? null;
        setPlan(ev.plan);
        setCity(ev.plan.city);
        break;
      case "clarify":
        setThinking(null);
        intentRef.current = ev.intent ?? intentRef.current;
        setItems((prev) => [
          ...prev,
          { id: uid(), kind: "clarify", text: ev.text, options: ev.options ?? [] },
        ]);
        break;
      case "error":
        setItems((prev) => [...prev, { id: uid(), kind: "msg", role: "assistant", content: ev.text }]);
        break;
      case "message": {
        setThinking(null);
        const current = streamingIdRef.current;
        if (!current) {
          const id = uid();
          streamingIdRef.current = id;
          setStreamingId(id);
          setItems((prev) => [...prev, { id, kind: "msg", role: "assistant", content: ev.text }]);
        } else {
          setItems((prev) =>
            prev.map((it) =>
              it.id === current && it.kind === "msg" ? { ...it, content: it.content + ev.text } : it,
            ),
          );
        }
        break;
      }
      case "done":
        if (planRef.current) {
          const note = planRef.current.feasibility.note;
          setItems((prev) => [...prev, { id: uid(), kind: "status", text: note }]);
        }
        streamingIdRef.current = null;
        setStreamingId(null);
        break;
    }
  };

  const send = async (text: string, scenario?: string, presetCity?: string, fresh = false) => {
    const readyFiles = attachments.filter((file) => file.status === "ready" && file.context);
    if (busy || (!text.trim() && readyFiles.length === 0)) return;
    setBusy(true);
    setThinking(readyFiles.length ? "正在结合附件理解你的行程 🧭" : "正在理解你的需求 🧭");
    setActiveScenario(scenario ?? null);
    streamingIdRef.current = null;
    planRef.current = null;
    // Example-scenario chips start a fresh conversation (replace, don't append),
    // so a demo never lands appended below a previous chat.
    if (fresh) {
      setPlan(null);
      intentRef.current = null;
    }

    const history = fresh ? [] : itemsToHistory(itemsRef.current);
    const userMsg: ChatItem = { id: uid(), kind: "msg", role: "user", content: text };
    setItems((prev) => (fresh ? [userMsg] : [...prev, userMsg]));

    const body: ChatRequestBody = {
      message: text || "请根据我上传的行程表整理并规划路线。",
      history,
      city: presetCity ?? city,
      origin: originRef.current,
      origin_status: originStatus,
      scenario: scenario ?? null,
      intent: fresh ? null : intentRef.current,
      file_contexts: readyFiles.map((file) => file.context!),
    };
    try {
      await streamChat(body, onEvent);
      if (readyFiles.length) {
        setAttachments((prev) => prev.filter((file) => file.status !== "ready"));
      }
    } catch (e) {
      setItems((prev) => [
        ...prev,
        { id: uid(), kind: "msg", role: "assistant", content: `抱歉，连接后端失败：${e}` },
      ]);
    } finally {
      setBusy(false);
      setThinking(null);
      streamingIdRef.current = null;
      setStreamingId(null);
    }
  };

  const onRelocate = () => {
    requestLocation(true);
  };

  const onChangeOrigin = () => {
    setOriginSearchFocusSignal((v) => v + 1);
    setItems((prev) => [
      ...prev,
      { id: uid(), kind: "status", text: "请在顶部“搜起点”输入框里输入新的出发地点，然后从下拉候选中选择。" },
    ]);
  };

  // User picked an alternative for a stop — replace it and re-route (no LLM).
  const onSwap = async (stopIndex: number, choice: POIChoice) => {
    const cur = planRef.current;
    if (!cur || busy || swappingIndex !== null) return;
    setSwappingIndex(stopIndex);
    const timeline = cur.timeline.map((s, i) =>
      i === stopIndex
        ? { ...s, name: choice.name, location: choice.location, rating: choice.rating, cost: choice.cost, open_info: null }
        : s,
    );
    const updated = await recomputeRoute(timeline, cur.city, cur.intent ?? null);
    if (updated) {
      const merged: Plan = { ...updated, understanding: cur.understanding ?? updated.understanding };
      planRef.current = merged;
      intentRef.current = merged.intent ?? intentRef.current;
      setPlan(merged);
    }
    setSwappingIndex(null);
  };

  const onAttachFile = async (file: File) => {
    if (busy) return;
    const id = uid();
    setAttachments((prev) => [...prev, { id, name: file.name, status: "parsing" }]);
    try {
      const context = await parseAttachment(file);
      setAttachments((prev) =>
        prev.map((item) => (item.id === id ? { ...item, status: "ready", context } : item)),
      );
      setItems((prev) => [
        ...prev,
        { id: uid(), kind: "status", text: `已解析附件「${file.name}」：${context.summary}` },
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "解析失败";
      setAttachments((prev) =>
        prev.map((item) => (item.id === id ? { ...item, status: "error", error: message } : item)),
      );
    }
  };

  const onRemoveAttachment = (id: string) => {
    if (busy) return;
    setAttachments((prev) => prev.filter((file) => file.id !== id));
  };

  const onPickPlaceCandidate = async (slotId: string, candidate: ResolvedPlaceCandidate) => {
    const cur = planRef.current;
    if (!cur || busy || swappingIndex !== null) return;
    const slot = cur.place_resolution?.slots.find((s) => s.id === slotId);
    if (!slot) return;
    const stopIndex = findStopIndexForSlot(cur, slotId);
    if (stopIndex < 0) return;
    setSwappingIndex(stopIndex);
    const timeline = cur.timeline.map((s, i) =>
      i === stopIndex
        ? {
            ...s,
            name: slot.role === "end" ? `回 ${candidate.name}` : candidate.name,
            location: candidate.location,
            rating: candidate.rating,
            cost: candidate.cost,
            open_info: slot.role === "start" ? `📍 ${candidate.name}` : null,
          }
        : s,
    );
    const updated = await recomputeRoute(timeline, cur.city, cur.intent ?? null);
    const placeResolution = updateSelectedPlace(cur, slotId, candidate);
    if (updated) {
      const merged: Plan = {
        ...updated,
        understanding: cur.understanding ?? updated.understanding,
        intent: updated.intent ?? cur.intent,
        place_resolution: placeResolution,
      };
      planRef.current = merged;
      intentRef.current = merged.intent ?? intentRef.current;
      setPlan(merged);
    } else {
      const merged: Plan = { ...cur, timeline, place_resolution: placeResolution };
      planRef.current = merged;
      setPlan(merged);
    }
    setSwappingIndex(null);
  };

  const reset = () => {
    if (busy) return;
    setItems([]);
    setPlan(null);
    planRef.current = null;
    intentRef.current = null;
    setThinking(null);
    setActiveScenario(null);
  };

  return (
    <>
      <TopBar
        locationLabel={locationLabel}
        locating={locating}
        onLocate={() => requestLocation(true)}
        city={city}
        onPickOrigin={pickOrigin}
        focusSearchSignal={originSearchFocusSignal}
      />

      <div className="tabbar">
        <span className="lbl">示例场景：</span>
        {PRESETS.map((p) => (
          <button
            key={p.id}
            className={`tab${activeScenario === p.id ? " on" : ""}`}
            disabled={busy}
            onClick={() => send(p.text, p.id, p.city, true)}
          >
            {p.label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="tab new-chat" disabled={busy || items.length === 0} onClick={reset}>
          ＋ 新对话
        </button>
      </div>

      <div className="layout">
        <ChatPanel
          items={items}
          thinking={thinking}
          streamingId={streamingId}
          busy={busy}
          onSend={(t) => send(t)}
          onAttachFile={onAttachFile}
          onRemoveAttachment={onRemoveAttachment}
          attachments={attachments}
        />
        <PlanPanel
          plan={plan}
          onRelocate={onRelocate}
          onChangeOrigin={onChangeOrigin}
          onSwap={onSwap}
          onPickPlaceCandidate={onPickPlaceCandidate}
          swappingIndex={swappingIndex}
        />
      </div>

      <div className="footer">
        RoamMind · 对话式行程规划助手
        {config && !config.llm_enabled && " · 示例模式（未配置 LLM Key，预置场景仍可演示）"}
        {config && config.llm_enabled && config.llm?.ok && ` · LLM 已连接：${config.llm.provider}/${config.llm.model}`}
        {config && config.llm_enabled && config.llm && !config.llm.ok && ` · LLM 连接异常：${config.llm.message}`}
        {config && config.llm_enabled && !config.llm && " · LLM Key 已配置，连接状态未知"}
        {config && config.amap_web_enabled && " · 高德实时数据已启用"}
        {config && !config.amap_web_enabled && " · 高德为示例数据"}
      </div>
    </>
  );
}

function sameLocation(a?: [number, number] | null, b?: [number, number] | null) {
  if (!a || !b) return false;
  return Math.abs(a[0] - b[0]) < 0.000001 && Math.abs(a[1] - b[1]) < 0.000001;
}

function stripReturnPrefix(name: string) {
  return name.replace(/^回\s*/, "");
}

function findStopIndexForSlot(plan: Plan, slotId: string) {
  const slot = plan.place_resolution?.slots.find((s) => s.id === slotId);
  if (!slot) return -1;
  if (slot.role === "start") return plan.timeline.findIndex((s) => s.kind === "start");
  if (slot.role === "end") return plan.timeline.findIndex((s) => s.kind === "end");
  if (slot.role === "fixed") {
    const selectedName = slot.selected?.name;
    return plan.timeline.findIndex(
      (s) =>
        s.kind === "fixed" &&
        (sameLocation(s.location, slot.selected?.location) || (selectedName ? s.name.includes(selectedName) : false)),
    );
  }
  const selected = slot.selected;
  return plan.timeline.findIndex(
    (s) =>
      s.kind === "poi" &&
      (sameLocation(s.location, selected?.location) ||
        (!!selected?.name && stripReturnPrefix(s.name) === selected.name) ||
        (!!selected?.name && s.name.includes(selected.name))),
  );
}

function updateSelectedPlace(plan: Plan, slotId: string, candidate: ResolvedPlaceCandidate) {
  if (!plan.place_resolution) return plan.place_resolution ?? null;
  return {
    ...plan.place_resolution,
    slots: plan.place_resolution.slots.map((slot) =>
      slot.id === slotId
        ? {
            ...slot,
            selected: candidate,
            status: "selected" as const,
          }
        : slot,
    ),
  };
}
