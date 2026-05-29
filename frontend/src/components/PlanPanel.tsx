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
}: {
  plan: Plan | null;
  onRelocate?: () => void;
  onChangeOrigin?: () => void;
  onSwap?: (stopIndex: number, choice: POIChoice) => void;
  onPickPlaceCandidate?: (slotId: string, candidate: PlaceCandidate) => void;
  swappingIndex?: number | null;
}) {
  const slots = plan?.place_resolution?.slots?.filter((slot) => slot.selected || slot.candidates.length) ?? [];
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const activeSlot = useMemo(
    () => slots.find((slot) => slot.id === activeSlotId) ?? slots[0] ?? null,
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
          <div className="mapcard">
            <div className="map-top">
              <div className="t">今日路线</div>
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
            </div>
            <MapView
              stops={plan.timeline}
              routePolyline={plan.summary.polyline}
              routeSegments={plan.summary.segments}
              candidateFocus={candidateFocus}
            />
          </div>
          <PlaceResolutionSummary
            resolution={plan.place_resolution ?? null}
            activeSlotId={activeSlot?.id ?? null}
            onSelectSlot={setActiveSlotId}
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
  resolution,
  activeSlotId,
  onSelectSlot,
  onPickCandidate,
}: {
  resolution: PlaceResolution | null;
  activeSlotId: string | null;
  onSelectSlot: (slotId: string) => void;
  onPickCandidate?: (slotId: string, candidate: PlaceCandidate) => void;
}) {
  const slots = resolution?.slots?.filter((slot) => slot.selected || slot.candidates.length) ?? [];
  if (!slots.length) return null;
  return (
    <div className="place-summary" aria-label="已确定地点">
      <div className="place-summary-head">
        <span>地点确认</span>
        <small>{resolution?.status === "places_ready" ? "已就绪" : "部分待确认"}</small>
      </div>
      <div className="place-slots">
        {slots.map((slot) => (
          <div className={`place-slot${slot.id === activeSlotId ? " active" : ""}`} key={slot.id}>
            <button className="place-slot-main" type="button" onClick={() => onSelectSlot(slot.id)}>
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
      return "固定";
    case "waypoint":
      return "途经";
    case "activity_poi":
      return "活动";
    default:
      return "地点";
  }
}
