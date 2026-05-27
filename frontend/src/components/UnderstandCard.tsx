import type { Understanding } from "../types";

function ChipRow({ label, chips, variant }: { label: string; chips: string[]; variant?: "mood" | "avoid" }) {
  if (chips.length === 0) return null;
  return (
    <div className="u-row">
      <span className="lbl">{label}</span>
      <div className="chips">
        {chips.map((c, i) => (
          <span className={`chip${variant ? " " + variant : ""}`} key={i}>
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function UnderstandCard({ u }: { u: Understanding }) {
  if (u.kind === "mood") {
    return (
      <div className="understand">
        <div className="u-head">
          <span className="ic">✦</span>
          {u.title}
        </div>
        <ChipRow label="心情" chips={u.mood_chips} variant="mood" />
        <ChipRow label="想要" chips={u.want_chips} />
        <ChipRow label="避免" chips={u.avoid_chips} variant="avoid" />
      </div>
    );
  }

  return (
    <div className="understand">
      <div className="u-head">
        <span className="ic ok">✓</span>
        {u.title}
      </div>
      <div className="steps">
        {u.steps.map((s, i) => (
          <div className={`step${i === u.steps.length - 1 && s.index === "终" ? " last" : ""}`} key={i}>
            <span className={`n${s.locked ? " lock" : ""}`}>{s.index}</span>
            <span>{s.label}</span>
          </div>
        ))}
      </div>
      {u.constraints.length > 0 && (
        <div className="cons">
          {u.constraints.map((c, i) => (
            <span className="ct" key={i}>
              {c}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
