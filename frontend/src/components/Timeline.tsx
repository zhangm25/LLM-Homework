import { useEffect, useRef, useState } from "react";
import type { Plan, POIChoice, Stop } from "../types";

function StopRow({
  stop,
  onRelocate,
  onChangeOrigin,
  onSwap,
  swapping,
}: {
  stop: Stop;
  onRelocate?: () => void;
  onChangeOrigin?: () => void;
  onSwap?: (choice: POIChoice) => void;
  swapping?: boolean;
}) {
  const isStart = stop.kind === "start";
  const isEnd = stop.kind === "end";
  const canSwap = !!onSwap && stop.kind === "poi" && (stop.alternatives?.length ?? 0) > 0;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className={`stop ${stop.kind}${open ? " menu-open" : ""}`}>
      <div className="marker">{stop.marker}</div>
      <div className="body">
        <div className="l1">
          <span className="time">{stop.time || "—"}</span>
          <span className="name">{stop.name}</span>
          <span className="meta">
            {isStart && stop.open_info && <span>{stop.open_info}</span>}
            {!isStart &&
              stop.tags.map((t, i) => (
                <span className="tag" key={i}>
                  {t}
                </span>
              ))}
            {stop.rating != null && <span className="star">★ {stop.rating}</span>}
            {stop.cost && <span>{stop.cost}</span>}
            {isStart && (
              <span className="start-actions">
                <button className="link link-btn" type="button" onClick={onRelocate}>
                  重新定位
                </button>
                <span className="sep">/</span>
                <button className="link link-btn" type="button" onClick={onChangeOrigin}>
                  改地点
                </button>
              </span>
            )}
            {isEnd && <span>终点</span>}
            {canSwap && (
              <span className="swap-wrap" ref={ref}>
                <span className="link" onClick={() => !swapping && setOpen((o) => !o)}>
                  {swapping ? "替换中…" : "换一个 ▾"}
                </span>
                {open && (
                  <span className="swap-menu">
                    {stop.alternatives.map((c, i) => (
                      <button
                        className="swap-item"
                        type="button"
                        key={`${c.name}-${i}`}
                        onClick={() => {
                          setOpen(false);
                          onSwap?.(c);
                        }}
                      >
                        <span className="swap-name">{c.name}</span>
                        <small className="swap-cmeta">
                          {c.rating != null ? `★ ${c.rating}` : ""}
                          {c.cost ? ` · ${c.cost}` : ""}
                          {c.address ? ` · ${c.address}` : ""}
                        </small>
                      </button>
                    ))}
                  </span>
                )}
              </span>
            )}
          </span>
        </div>
        {isStart ? (
          stop.why && <div className="dur">{stop.why}</div>
        ) : (
          <>
            {stop.why && <div className="why">{stop.why}</div>}
            {stop.leg && <div className="dur">{stop.leg}</div>}
          </>
        )}
      </div>
    </div>
  );
}

export default function Timeline({
  plan,
  onRelocate,
  onChangeOrigin,
  onSwap,
  swappingIndex,
}: {
  plan: Plan;
  onRelocate?: () => void;
  onChangeOrigin?: () => void;
  onSwap?: (stopIndex: number, choice: POIChoice) => void;
  swappingIndex?: number | null;
}) {
  const hasFixed = plan.timeline.some((s) => s.kind === "fixed");
  return (
    <>
      <div className="tl-title">
        <h3>时间轴</h3>
        {hasFixed ? (
          <span className="feas">{plan.feasibility.note}</span>
        ) : (
          <span className="reason">已按营业时间与路程排好</span>
        )}
      </div>
      <div className="timeline">
        {plan.timeline.map((stop, i) => (
          <StopRow
            stop={stop}
            key={`${i}-${stop.name}`}
            onRelocate={stop.kind === "start" ? onRelocate : undefined}
            onChangeOrigin={stop.kind === "start" ? onChangeOrigin : undefined}
            onSwap={onSwap ? (c) => onSwap(i, c) : undefined}
            swapping={swappingIndex === i}
          />
        ))}
      </div>
    </>
  );
}
