import { useMemo, useState } from "react";
import type { PlaceCandidate, PlaceResolution, PlaceSlot, Plan, POIChoice } from "../types";
import MapView from "./MapView";
import Timeline from "./Timeline";
import NavButton from "./NavButton";
import { downloadItineraryHtml, openItineraryHtml } from "../lib/exportItinerary";

export default function PlanPanel({
  plan,
  onRelocate,
  onChangeOrigin,
  onSwap,
  onPickPlaceCandidate,
  swappingIndex,
  mapExpanded = false,
  onToggleMapExpanded,
  amapJsKey = "",
  amapJsSecurityCode = "",
}: {
  plan: Plan | null;
  onRelocate?: () => void;
  onChangeOrigin?: () => void;
  onSwap?: (stopIndex: number, choice: POIChoice) => void;
  onPickPlaceCandidate?: (slotId: string, candidate: PlaceCandidate) => void;
  swappingIndex?: number | null;
  mapExpanded?: boolean;
  onToggleMapExpanded?: () => void;
  amapJsKey?: string;
  amapJsSecurityCode?: string;
}) {
  const slots = useMemo(() => orderedPlaceSlots(plan), [plan]);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const activeSlot = useMemo(
    () => slots.find((slot) => slot.id === activeSlotId) ?? null,
    [activeSlotId, slots],
  );
  const candidateFocus =
    activeSlot && activeSlot.candidates.length
      ? {
          slotId: activeSlot.id,
          candidates: activeSlot.candidates,
          selectedId: activeSlot.selected?.id ?? null,
          onPick: (candidate: PlaceCandidate) => onPickPlaceCandidate?.(activeSlot.id, candidate),
        }
      : null;

  return (
    <div className="plan">
      <div className="panel-title">
        <span className="dot sage" />
        行程方案
        <span className="hint">{plan?.panel_hint || "等待规划…"}</span>
      </div>

      {plan ? (
        <>
          <div className={`mapcard${mapExpanded ? " expanded" : ""}`}>
            <div className="map-top">
              <div className="t">今日路线</div>
              <div className="map-actions">
                <div className="stat">
                  <span>
                    全程 <b>{plan.summary.total_distance_text}</b>
                  </span>
                  <span>
                    约 <b>{plan.summary.total_duration_text}</b>
                  </span>
                  <span>
                    <b>{plan.summary.stop_count}</b> 站
                  </span>
                </div>
                <button
                  className="map-expand-btn"
                  type="button"
                  onClick={onToggleMapExpanded}
                  aria-pressed={mapExpanded}
                  title={mapExpanded ? "缩小地图，恢复对话框" : "放大地图，暂时隐藏对话框"}
                >
                  <span aria-hidden="true">{mapExpanded ? "↙" : "↗"}</span>
                  {mapExpanded ? "缩小地图" : "放大地图"}
                </button>
              </div>
            </div>
            <MapView
              stops={plan.timeline}
              routePolyline={plan.summary.polyline}
              routeSegments={plan.summary.segments}
              candidateFocus={candidateFocus}
              amapJsKey={amapJsKey}
              amapJsSecurityCode={amapJsSecurityCode}
            />
          </div>
          <PlaceResolutionSummary
            status={plan.place_resolution?.status ?? null}
            slots={slots}
            activeSlotId={activeSlotId}
            onShowRoute={() => setActiveSlotId(null)}
            onToggleSlot={(slotId) => setActiveSlotId((current) => (current === slotId ? null : slotId))}
            onPickCandidate={onPickPlaceCandidate}
          />
          <Timeline
            plan={plan}
            onRelocate={onRelocate}
            onChangeOrigin={onChangeOrigin}
            onSwap={onSwap}
            swappingIndex={swappingIndex}
          />
          <div className="export-actions">
            <button className="export-btn" type="button" onClick={() => downloadItineraryHtml(plan)}>
              保存行程网页
              <small>下载 HTML 文件</small>
            </button>
            <button className="export-btn" type="button" onClick={() => openItineraryHtml(plan)}>
              预览行程网页
              <small>新标签打开</small>
            </button>
          </div>
          <NavButton plan={plan} />
        </>
      ) : (
        <div className="mapcard mapcard-empty">
          <div className="plan-empty">说一句话，我就把行程、地图与时间轴排在这里 ✦</div>
        </div>
      )}
    </div>
  );
}

function PlaceResolutionSummary({
  status,
  slots,
  activeSlotId,
  onShowRoute,
  onToggleSlot,
  onPickCandidate,
}: {
  status: PlaceResolution["status"] | null;
  slots: PlaceSlot[];
  activeSlotId: string | null;
  onShowRoute: () => void;
  onToggleSlot: (slotId: string) => void;
  onPickCandidate?: (slotId: string, candidate: PlaceCandidate) => void;
}) {
  if (!slots.length) return null;
  return (
    <div className="place-summary" aria-label="已确定地点">
      <div className="place-summary-head">
        <span>地点确认</span>
        <div className="place-summary-actions">
          <button className={`route-view-btn${activeSlotId ? "" : " active"}`} type="button" onClick={onShowRoute}>
            显示行程
          </button>
          <small>{status === "places_ready" ? "已就绪" : "部分待确认"}</small>
        </div>
      </div>
      <div className="place-slots">
        {slots.map((slot) => (
          <div className={`place-slot${slot.id === activeSlotId ? " active" : ""}`} key={slot.id}>
            <button className="place-slot-main" type="button" onClick={() => onToggleSlot(slot.id)}>
              <span className="place-role">{roleLabel(slot.role)}</span>
              <span className="place-main" title={slot.selected?.address || slot.selected?.name || slot.query}>
                {slot.selected?.name || slot.query}
              </span>
              {slot.candidates.length > 1 ? <span className="place-count">{slot.candidates.length} 个候选</span> : null}
              {slot.selected?.address ? <span className="place-address">{slot.selected.address}</span> : null}
            </button>
            {slot.id === activeSlotId && slot.candidates.length > 1 ? (
              <CandidateList slot={slot} onPickCandidate={onPickCandidate} />
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function CandidateList({
  slot,
  onPickCandidate,
}: {
  slot: PlaceSlot;
  onPickCandidate?: (slotId: string, candidate: PlaceCandidate) => void;
}) {
  return (
    <div className="place-candidates">
      {slot.candidates.map((candidate) => {
        const selected = candidate.id === slot.selected?.id;
        return (
          <button
            className={`place-candidate${selected ? " selected" : ""}`}
            type="button"
            key={candidate.id}
            onClick={() => onPickCandidate?.(slot.id, candidate)}
          >
            <span>{candidate.name}</span>
            <small>
              {candidate.rating != null ? `★ ${candidate.rating}` : "候选地点"}
              {candidate.address ? ` · ${candidate.address}` : ""}
            </small>
          </button>
        );
      })}
    </div>
  );
}

function roleLabel(role: string) {
  switch (role) {
    case "start":
      return "起点";
    case "end":
      return "终点";
    case "fixed":
    case "waypoint":
    case "activity_poi":
      return "途径";
    default:
      return "途径";
  }
}

function orderedPlaceSlots(plan: Plan | null) {
  const slots = plan?.place_resolution?.slots?.filter((slot) => slot.selected || slot.candidates.length) ?? [];
  if (!plan) return slots;
  return slots
    .map((slot, originalIndex) => ({
      slot,
      originalIndex,
      timelineIndex: placeSlotTimelineIndex(plan, slot, originalIndex),
    }))
    .sort((a, b) => a.timelineIndex - b.timelineIndex || a.originalIndex - b.originalIndex)
    .map((item) => item.slot);
}

function placeSlotTimelineIndex(plan: Plan, slot: PlaceSlot, fallback: number) {
  const exact = plan.timeline.findIndex((stop) => stopMatchesSlot(stop, slot));
  if (exact >= 0) return exact;

  if (slot.role === "start") {
    const start = plan.timeline.findIndex((stop) => stop.kind === "start");
    return start >= 0 ? start : -1;
  }
  if (slot.role === "end") {
    const end = plan.timeline.findIndex((stop) => stop.kind === "end");
    return end >= 0 ? end : plan.timeline.length + fallback;
  }
  if (slot.role === "fixed") {
    const fixed = plan.timeline.findIndex((stop) => stop.kind === "fixed");
    return fixed >= 0 ? fixed : plan.timeline.length + fallback;
  }
  return plan.timeline.length + fallback;
}

function stopMatchesSlot(stop: Plan["timeline"][number], slot: PlaceSlot) {
  if (slot.role === "start" && stop.kind !== "start") return false;
  if (slot.role === "end" && stop.kind !== "end") return false;
  if (slot.role === "fixed" && stop.kind !== "fixed") return false;
  if ((slot.role === "waypoint" || slot.role === "activity_poi") && stop.kind !== "poi") return false;

  const selected = slot.selected;
  if (sameLocation(stop.location, selected?.location)) return true;

  const stopName = stripReturnPrefix(stop.name);
  const selectedName = selected?.name ?? "";
  const query = slot.query || slot.source_text;
  return Boolean(
    (selectedName && (stopName === selectedName || stopName.includes(selectedName) || selectedName.includes(stopName))) ||
      (query && (stopName === query || stopName.includes(query) || query.includes(stopName))),
  );
}

function sameLocation(a?: [number, number] | null, b?: [number, number] | null) {
  if (!a || !b) return false;
  return Math.abs(a[0] - b[0]) < 0.000001 && Math.abs(a[1] - b[1]) < 0.000001;
}

function stripReturnPrefix(name: string) {
  return name.replace(/^回\s*/, "");
}
