import { useEffect, useRef, useState } from "react";
import { suggestPlaces, type PlaceCandidate } from "../api";

export default function TopBar({
  locationLabel,
  locating,
  onLocate,
  city,
  onPickOrigin,
}: {
  locationLabel: string;
  locating: boolean;
  onLocate: () => void;
  city: string;
  onPickOrigin: (c: PlaceCandidate) => void;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<PlaceCandidate[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);

  // Debounced search as you type (>= 2 chars). The user picks from the list,
  // so we never silently commit a single wrong geocode result.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setCandidates([]);
      setOpen(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    setOpen(true);
    const timer = setTimeout(async () => {
      const list = await suggestPlaces(q, city);
      setCandidates(list);
      setLoading(false);
    }, 300);
    return () => clearTimeout(timer);
  }, [query, city]);

  // Close the dropdown on an outside click.
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const pick = (c: PlaceCandidate) => {
    onPickOrigin(c);
    setQuery("");
    setCandidates([]);
    setOpen(false);
  };

  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">✦</span> RoamMind <small>随心行</small>
      </div>
      <div className="tagline">说一句话，我帮你把行程安排好</div>
      <div className="spacer" />
      <div className="locbox">
        <button className="pill loc-pill" type="button" onClick={onLocate} disabled={locating}>
          📍 {locating ? "定位中…" : locationLabel}
        </button>
        <div className="manual-loc" ref={boxRef}>
          <input
            value={query}
            placeholder="搜起点，如 清华大学四教"
            onChange={(e) => setQuery(e.target.value)}
            onFocus={() => {
              if (candidates.length || query.trim().length >= 2) setOpen(true);
            }}
          />
          {open && (
            <div className="loc-menu">
              {loading && <div className="loc-empty">搜索中…</div>}
              {!loading && candidates.length === 0 && (
                <div className="loc-empty">没找到「{query.trim()}」，换个更具体的名字试试</div>
              )}
              {!loading &&
                candidates.map((c, i) => (
                  <button className="loc-item" type="button" key={`${c.label}-${i}`} onClick={() => pick(c)}>
                    <span className="loc-name">{c.label}</span>
                    {c.address && <small className="loc-addr">{c.address}</small>}
                  </button>
                ))}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
