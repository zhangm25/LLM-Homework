import { useState } from "react";
import type { Plan } from "../types";
import { launchNav } from "../lib/amapNav";

export default function NavButton({ plan }: { plan: Plan }) {
  const [note, setNote] = useState<string | null>(null);
  const disabled = !plan.nav.web_uri;
  const showSegmentLinks = !plan.nav.web_supports_all_waypoints && plan.nav.segment_web_uris.length > 1;
  const locatedStops = plan.timeline.filter((s) => s.location);

  const onClick = () => {
    const result = launchNav(plan.nav);
    setNote(result.message ?? null);
  };

  return (
    <>
      <button className="navbtn" onClick={onClick} disabled={disabled}>
        🧭 一键唤起高德导航 <small>（手机上将打开高德 App）</small>
      </button>
      <div className="subnote">
        {disabled
          ? "示例占位暂无坐标 · 配置高德 Key 后可一键唤起导航"
          : `含 ${plan.nav.waypoint_count} 个途经点${plan.nav.waypoint_names.length ? `：${plan.nav.waypoint_names.join("、")}` : ""} · iOS / 安卓 App 使用完整路线`}
      </div>
      {!disabled && !plan.nav.web_supports_all_waypoints && (
        <div className="subnote warn">
          高德网页兜底最多只能带 1 个途经点；手机 App 深链会携带全部途经点。
        </div>
      )}
      {showSegmentLinks && (
        <div className="segment-nav">
          {plan.nav.segment_web_uris.map((uri, i) => {
            const from = locatedStops[i]?.name ?? `第 ${i + 1} 段起点`;
            const to = locatedStops[i + 1]?.name ?? `第 ${i + 1} 段终点`;
            return (
              <button
                key={`${uri}-${i}`}
                type="button"
                onClick={() => window.open(uri, "_blank", "noopener")}
              >
                {i + 1}. {from} → {to}
              </button>
            );
          })}
        </div>
      )}
      {note && (
        <div className="subnote warn">
          {note}
        </div>
      )}
    </>
  );
}
