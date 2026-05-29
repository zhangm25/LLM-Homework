import { useEffect, useRef, useState } from "react";
import AMapLoader from "@amap/amap-jsapi-loader";
import type { PlaceCandidate, RouteSegment, Stop } from "../types";

const JS_KEY = import.meta.env.VITE_AMAP_JS_KEY;
const SECURITY_CODE = import.meta.env.VITE_AMAP_JS_SECURITY_CODE;

const W = 640;
const H = 300;
const PAD = 70;
const ROUTE_COLOR = "#C96442";
const SEGMENT_COLORS = ["#C96442", "#4F7163", "#C98A33", "#6F5D91", "#4E7E9E"];

type LngLat = [number, number];

interface CandidateFocus {
  slotId: string;
  candidates: PlaceCandidate[];
  selectedId?: string | null;
  onPick?: (candidate: PlaceCandidate) => void;
}

function pinLabel(stop: Stop, index: number, total: number): string {
  if (stop.kind === "start") return "起";
  if (stop.kind === "end") return "家";
  if (stop.kind === "fixed") return "会";
  if (index === total - 1) return "终";
  return stop.marker || String(index);
}

function pinHtml(stop: Stop, index: number, total: number): string {
  const dark = stop.kind === "start" || stop.kind === "end" || stop.kind === "fixed";
  return `<div style="width:24px;height:24px;border-radius:50%;background:${
    dark ? "#211F1B" : ROUTE_COLOR
  };color:#fff;display:grid;place-items:center;font-size:12px;font-weight:700;box-shadow:0 2px 6px rgba(0,0,0,.3)">${pinLabel(
    stop,
    index,
    total,
  )}</div>`;
}

function candidateHtml(selected: boolean): string {
  return `<div style="min-width:28px;height:28px;border-radius:14px;background:${
    selected ? "#211F1B" : ROUTE_COLOR
  };color:#fff;display:grid;place-items:center;padding:0 8px;font-size:12px;font-weight:700;box-shadow:0 2px 8px rgba(0,0,0,.28);border:2px solid #fff">${selected ? "选" : "候"}</div>`;
}

// Project [lng,lat] coords into the SVG viewBox over a shared bounding box.
function makeProjector(coords: LngLat[]) {
  const lngs = coords.map((c) => c[0]);
  const lats = coords.map((c) => c[1]);
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const spanLng = maxLng - minLng || 1;
  const spanLat = maxLat - minLat || 1;
  return (lng: number, lat: number) => ({
    x: PAD + ((lng - minLng) / spanLng) * (W - 2 * PAD),
    y: H - PAD - ((lat - minLat) / spanLat) * (H - 2 * PAD), // flip lat (north up)
  });
}

/** SVG fallback. If the backend supplied a real route polyline, we draw the
 *  actual road shape; otherwise we lay stops out along a gentle S-curve. */
function SvgMap({ stops, routePolyline }: { stops: Stop[]; routePolyline: LngLat[] }) {
  const located = stops.filter((s) => s.location) as (Stop & { location: LngLat })[];
  const hasRoute = routePolyline.length >= 2;
  const allLocated = located.length >= 2;

  let path = "";
  let pins: { x: number; y: number; stop: Stop }[] = [];

  if (hasRoute || allLocated) {
    const geoForBox: LngLat[] = hasRoute
      ? [...routePolyline, ...located.map((s) => s.location)]
      : located.map((s) => s.location);
    const project = makeProjector(geoForBox);
    const linePts = (hasRoute ? routePolyline : located.map((s) => s.location)).map(([lng, lat]) =>
      project(lng, lat),
    );
    path = linePts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    pins = located.map((s) => ({ ...project(s.location[0], s.location[1]), stop: s }));
  } else {
    const n = Math.max(stops.length, 2);
    pins = stops.map((s, i) => ({
      x: PAD + (i * (W - 2 * PAD)) / (n - 1),
      y: H / 2 + Math.sin(i * 1.25) * 58,
      stop: s,
    }));
    path = pins.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(0)},${p.y.toFixed(0)}`).join(" ");
  }

  return (
    <svg className="map" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      <rect x="60" y="40" width="120" height="64" rx="16" fill="#E5EDE4" />
      <rect x="430" y="170" width="150" height="90" rx="18" fill="#E5EDE4" />
      <path
        d="M380,28 C430,66 470,76 540,58 C590,44 610,66 630,58"
        stroke="#D4E2E9" strokeWidth="13" fill="none" strokeLinecap="round"
      />
      <path className="route" d={path} key={path} />
      {pins.map((p, i) => {
        const ends =
          p.stop.kind === "start" || p.stop.kind === "end" || p.stop.kind === "fixed";
        return (
          <g
            className={`pin${ends ? " ends" : ""}`}
            key={`${i}-${p.stop.name}`}
            transform={`translate(${p.x.toFixed(0)},${p.y.toFixed(0)})`}
            style={{ animationDelay: `${0.25 + i * 0.12}s` }}
          >
            <circle className="ring" r="15" />
            <circle className="bg" r="12" />
            <text dy="4.5">{pinLabel(p.stop, i, pins.length)}</text>
          </g>
        );
      })}
    </svg>
  );
}

function SvgSegmentMap({
  stops,
  routeSegments,
}: {
  stops: Stop[];
  routeSegments: RouteSegment[];
}) {
  const located = stops.filter((s) => s.location) as (Stop & { location: LngLat })[];
  const coords = [
    ...routeSegments.flatMap((seg) => seg.polyline),
    ...located.map((s) => s.location),
  ];
  if (coords.length < 2) return <SvgMap stops={stops} routePolyline={[]} />;

  const project = makeProjector(coords);
  const pins = located.map((s) => ({ ...project(s.location[0], s.location[1]), stop: s }));
  return (
    <svg className="map" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      <rect x="60" y="40" width="120" height="64" rx="16" fill="#E5EDE4" />
      <rect x="430" y="170" width="150" height="90" rx="18" fill="#E5EDE4" />
      <path
        d="M380,28 C430,66 470,76 540,58 C590,44 610,66 630,58"
        stroke="#D4E2E9" strokeWidth="13" fill="none" strokeLinecap="round"
      />
      {routeSegments.map((seg, i) => {
        const pts = seg.polyline.map(([lng, lat]) => project(lng, lat));
        const d = pts.map((p, j) => `${j === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
        return (
          <path
            className="route seg-route"
            d={d}
            key={`${seg.from_index}-${seg.to_index}-${i}`}
            style={{
              stroke: SEGMENT_COLORS[i % SEGMENT_COLORS.length],
              animationDelay: `${0.12 * i}s`,
            }}
          />
        );
      })}
      {pins.map((p, i) => {
        const ends = p.stop.kind === "start" || p.stop.kind === "end" || p.stop.kind === "fixed";
        return (
          <g
            className={`pin${ends ? " ends" : ""}`}
            key={`${i}-${p.stop.name}`}
            transform={`translate(${p.x.toFixed(0)},${p.y.toFixed(0)})`}
            style={{ animationDelay: `${0.25 + i * 0.12}s` }}
          >
            <circle className="ring" r="15" />
            <circle className="bg" r="12" />
            <text dy="4.5">{pinLabel(p.stop, i, pins.length)}</text>
          </g>
        );
      })}
    </svg>
  );
}

function SvgCandidateMap({ focus }: { focus: CandidateFocus }) {
  const candidates = focus.candidates.filter((c) => c.location?.length === 2);
  if (!candidates.length) return <SvgMap stops={[]} routePolyline={[]} />;
  const project = makeProjector(candidates.map((c) => c.location));
  return (
    <svg className="map" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      <rect x="60" y="40" width="120" height="64" rx="16" fill="#E5EDE4" />
      <rect x="430" y="170" width="150" height="90" rx="18" fill="#E5EDE4" />
      <path
        d="M380,28 C430,66 470,76 540,58 C590,44 610,66 630,58"
        stroke="#D4E2E9" strokeWidth="13" fill="none" strokeLinecap="round"
      />
      {candidates.map((candidate, i) => {
        const p = project(candidate.location[0], candidate.location[1]);
        const selected = candidate.id === focus.selectedId;
        return (
          <g
            className={`candidate-pin${selected ? " selected" : ""}`}
            key={`${candidate.id}-${i}`}
            transform={`translate(${p.x.toFixed(0)},${p.y.toFixed(0)})`}
            onClick={() => focus.onPick?.(candidate)}
            role="button"
            tabIndex={0}
            aria-label={`选择 ${candidate.name}`}
          >
            <circle className="ring" r="17" />
            <circle className="bg" r="13" />
            <text dy="4.5">{selected ? "选" : String(i + 1)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function MapView({
  stops,
  routePolyline = [],
  routeSegments = [],
  candidateFocus = null,
}: {
  stops: Stop[];
  routePolyline?: LngLat[];
  routeSegments?: RouteSegment[];
  candidateFocus?: CandidateFocus | null;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  const useReal = Boolean(JS_KEY) && !failed;

  useEffect(() => {
    if (!useReal || !ref.current) return;
    let cancelled = false;
    let map: any = null;

    if (SECURITY_CODE) window._AMapSecurityConfig = { securityJsCode: SECURITY_CODE };
    AMapLoader.load({ key: JS_KEY as string, version: "2.0", plugins: [] })
      .then((AMap: any) => {
        if (cancelled || !ref.current) return;
        map = new AMap.Map(ref.current, { zoom: 12, viewMode: "2D", mapStyle: "amap://styles/whitesmoke" });
        const focusedCandidates = candidateFocus?.candidates.filter((c) => c.location?.length === 2) ?? [];
        if (focusedCandidates.length) {
          focusedCandidates.forEach((candidate) => {
            const marker = new AMap.Marker({
              position: candidate.location,
              anchor: "center",
              content: candidateHtml(candidate.id === candidateFocus?.selectedId),
            });
            marker.on("click", () => candidateFocus?.onPick?.(candidate));
            map.add(marker);
          });
          map.setFitView(null, false, [48, 48, 48, 48]);
          return;
        }
        const located = stops.filter((s) => s.location);
        // real road geometry if the backend supplied it, else connect the stops
        const useSegments = routeSegments.filter((seg) => seg.polyline.length >= 2);
        const path = routePolyline.length >= 2 ? routePolyline : located.map((s) => s.location);
        if (useSegments.length) {
          useSegments.forEach((seg, i) => {
            map.add(
              new AMap.Polyline({
                path: seg.polyline,
                strokeColor: SEGMENT_COLORS[i % SEGMENT_COLORS.length],
                strokeWeight: 6,
                strokeOpacity: 0.95,
                lineJoin: "round",
                lineCap: "round",
                zIndex: 20 + i,
              }),
            );
          });
        } else if (path.length >= 2) {
          map.add(
            new AMap.Polyline({
              path,
              strokeColor: ROUTE_COLOR,
              strokeWeight: 6,
              strokeOpacity: 0.95,
              lineJoin: "round",
              lineCap: "round",
              showDir: routePolyline.length < 2, // arrows only when drawing straight legs
            }),
          );
        }
        located.forEach((s, i) =>
          map.add(new AMap.Marker({ position: s.location, anchor: "center", content: pinHtml(s, i, located.length) })),
        );
        if (path.length) map.setFitView(null, false, [40, 40, 40, 40]);
      })
      .catch(() => {
        if (!cancelled) setFailed(true); // bad key / offline -> SVG fallback
      });

    return () => {
      cancelled = true;
      if (map) map.destroy();
    };
  }, [stops, routePolyline, routeSegments, useReal, candidateFocus]);

  return (
    <div className="mapwrap">
      {useReal ? (
        <div className="map-real" ref={ref} />
      ) : candidateFocus?.candidates.length ? (
        <SvgCandidateMap focus={candidateFocus} />
      ) : routeSegments.length > 0 ? (
        <SvgSegmentMap stops={stops} routeSegments={routeSegments} />
      ) : (
        <SvgMap stops={stops} routePolyline={routePolyline} />
      )}
      <div className="badge-disc">
        {useReal
          ? candidateFocus?.candidates.length ? "高德实时地图 · 候选地点" : "高德实时地图 · 分段路线"
          : candidateFocus?.candidates.length
            ? "候选地点 · 点击更换"
          : routePolyline.length >= 2
            ? "真实路线（后端高德）· 分段配色"
            : "示例数据 · 实际地点由高德实时检索填充"}
      </div>
    </div>
  );
}
