import type { PlaceResolution, Plan, POIChoice } from "../types";
import MapView from "./MapView";
import Timeline from "./Timeline";
import NavButton from "./NavButton";
import { downloadItineraryHtml, openItineraryHtml } from "../lib/exportItinerary";

export default function PlanPanel({
  plan,
  onRelocate,
  onChangeOrigin,
  onSwap,
  swappingIndex,
}: {
  plan: Plan | null;
  onRelocate?: () => void;
  onChangeOrigin?: () => void;
  onSwap?: (stopIndex: number, choice: POIChoice) => void;
  swappingIndex?: number | null;
}) {
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
            />
          </div>
          <PlaceResolutionSummary resolution={plan.place_resolution ?? null} />
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

function PlaceResolutionSummary({ resolution }: { resolution: PlaceResolution | null }) {
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
          <div className="place-slot" key={slot.id}>
            <span className="place-role">{roleLabel(slot.role)}</span>
            <span className="place-main" title={slot.selected?.address || slot.selected?.name || slot.query}>
              {slot.selected?.name || slot.query}
            </span>
            {slot.candidates.length > 1 ? <span className="place-count">{slot.candidates.length} 个候选</span> : null}
            {slot.selected?.address ? <span className="place-address">{slot.selected.address}</span> : null}
          </div>
        ))}
      </div>
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
