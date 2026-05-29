import type { Plan, Stop } from "../types";

function esc(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function attr(value: unknown): string {
  return esc(value).replaceAll("\n", " ");
}

function slug(value: string): string {
  const clean = value
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "")
    .replace(/\s+/g, "-");
  return clean || "RoamMind";
}

function amapMarkerLink(stop: Stop): string | null {
  if (!stop.location) return null;
  const [lng, lat] = stop.location;
  return `https://uri.amap.com/marker?position=${lng},${lat}&name=${encodeURIComponent(stop.name)}`;
}

function navLinkForStop(plan: Plan, stopIndex: number): string | null {
  const segmentIndex = plan.summary.segments.findIndex((s) => s.to_index === stopIndex);
  if (segmentIndex < 0) return null;
  return plan.nav.segment_web_uris[segmentIndex] ?? null;
}

function stopType(stop: Stop): string {
  if (stop.kind === "start") return "起点";
  if (stop.kind === "end") return "终点";
  if (stop.kind === "fixed") return "固定日程";
  return "行程地点";
}

function stopMeta(stop: Stop): string[] {
  const out = [...(stop.tags ?? [])];
  if (stop.rating != null) out.push(`评分 ${stop.rating}`);
  if (stop.cost) out.push(stop.cost);
  if (stop.open_info && stop.kind !== "start") out.push(stop.open_info);
  if (stop.dwell_min && stop.kind === "poi") out.push(`停留约 ${stop.dwell_min} 分钟`);
  return out;
}

function renderStops(plan: Plan): string {
  return plan.timeline
    .map((stop, index) => {
      const mapLink = amapMarkerLink(stop);
      const navLink = navLinkForStop(plan, index);
      const meta = stopMeta(stop);
      return `
        <article class="stop ${esc(stop.kind)}">
          <div class="rail">
            <div class="marker">${esc(stop.marker || String(index + 1))}</div>
            ${index < plan.timeline.length - 1 ? '<div class="line"></div>' : ""}
          </div>
          <div class="card">
            <div class="topline">
              <span class="time">${esc(stop.time || "未定")}</span>
              <span class="kind">${esc(stopType(stop))}</span>
            </div>
            <h2>${esc(stop.name)}</h2>
            ${meta.length ? `<div class="chips">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</div>` : ""}
            ${stop.why ? `<p class="why">${esc(stop.why)}</p>` : ""}
            ${stop.leg ? `<p class="leg">${esc(stop.leg)}</p>` : ""}
            <div class="actions">
              ${navLink ? `<a href="${attr(navLink)}" target="_blank" rel="noopener">导航到这里</a>` : ""}
              ${mapLink ? `<a href="${attr(mapLink)}" target="_blank" rel="noopener">查看地点</a>` : ""}
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderSegments(plan: Plan): string {
  if (!plan.summary.segments.length) return "";
  return `
    <section class="section">
      <h2 class="section-title">分段导航</h2>
      <div class="segments">
        ${plan.summary.segments
          .map((seg, index) => {
            const uri = plan.nav.segment_web_uris[index];
            return `
              <div class="segment">
                <div>
                  <strong>${esc(seg.from_name)} → ${esc(seg.to_name)}</strong>
                  <span>${esc(seg.mode === "walking" ? "步行" : seg.mode === "driving" ? "驾车" : seg.mode === "transit" ? "公交" : "自动")}</span>
                </div>
                ${uri ? `<a href="${attr(uri)}" target="_blank" rel="noopener">打开高德</a>` : ""}
              </div>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

export function itineraryHtml(plan: Plan): string {
  const generatedAt = new Date().toLocaleString("zh-CN", { hour12: false });
  const fullRoute = plan.nav.web_uri;
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
  <title>${esc(plan.city)}行程 - RoamMind</title>
  <style>
    :root{color-scheme:light;--bg:#f6f2ea;--card:#fff;--ink:#211f1b;--muted:#746f66;--line:#e5ddd1;--accent:#c96442;--sage:#4f7163}
    *{box-sizing:border-box}
    body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.58}
    .wrap{max-width:760px;margin:0 auto;padding:20px 14px 36px}
    header{padding:18px 2px 14px}
    .brand{font-size:13px;color:var(--muted);font-weight:650;letter-spacing:.08em;text-transform:uppercase}
    h1{font-size:26px;line-height:1.18;margin:8px 0 10px;letter-spacing:0}
    .summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}
    .metric{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 10px}
    .metric small{display:block;color:var(--muted);font-size:11px}
    .metric b{display:block;font-size:16px;margin-top:2px}
    .note{background:#eef5f0;border:1px solid #d8e5dd;color:#365c4e;border-radius:12px;padding:11px 12px;font-size:13px}
    .route-action{display:flex;gap:8px;margin-top:12px}
    a{color:var(--accent);text-decoration:none}
    .route-action a,.actions a,.segment a{display:inline-flex;align-items:center;justify-content:center;border:1px solid #efd5c8;background:#fff6f1;color:#a84f30;border-radius:10px;padding:9px 11px;font-size:13px;font-weight:650}
    .section{margin-top:22px}
    .section-title{font-size:17px;margin:0 0 10px}
    .timeline{display:flex;flex-direction:column;gap:0}
    .stop{display:grid;grid-template-columns:34px 1fr;gap:10px}
    .rail{display:flex;flex-direction:column;align-items:center}
    .marker{width:30px;height:30px;border-radius:10px;background:#f5e6df;color:#a84f30;display:grid;place-items:center;font-weight:800;font-size:13px}
    .stop.start .marker,.stop.end .marker{background:#26231f;color:#fff}
    .stop.fixed .marker{background:#e9e1f4;color:#5b4b8a}
    .line{width:2px;flex:1;min-height:16px;background:var(--line)}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 13px 12px;margin-bottom:10px;box-shadow:0 6px 20px rgba(33,31,27,.06)}
    .topline{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}
    .time{color:var(--accent);font-weight:800}
    .kind{margin-left:auto}
    .card h2{font-size:17px;line-height:1.3;margin:5px 0 8px}
    .chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
    .chips span{font-size:11px;color:#3f6657;background:#edf5f0;border:1px solid #d8e5dd;border-radius:999px;padding:3px 8px}
    .why,.leg{font-size:13px;margin:6px 0;color:var(--muted)}
    .leg{font-size:12px;color:#8b8478}
    .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
    .segments{display:flex;flex-direction:column;gap:8px}
    .segment{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px;display:flex;align-items:center;justify-content:space-between;gap:10px}
    .segment strong{display:block;font-size:13px}
    .segment span{display:block;font-size:12px;color:var(--muted);margin-top:2px}
    footer{margin-top:22px;color:var(--muted);font-size:11px;text-align:center}
    @media (max-width:520px){
      .wrap{padding:16px 10px 28px}
      h1{font-size:23px}
      .summary{grid-template-columns:1fr 1fr}
      .metric:last-child{grid-column:1 / -1}
      .route-action,.segment{align-items:stretch;flex-direction:column}
      .route-action a,.segment a,.actions a{width:100%}
    }
  </style>
</head>
<body>
  <main class="wrap">
    <header>
      <div class="brand">RoamMind 行程单</div>
      <h1>${esc(plan.city)}行程</h1>
      <p class="note">${esc(plan.feasibility.note)}</p>
      <div class="summary">
        <div class="metric"><small>总距离</small><b>${esc(plan.summary.total_distance_text)}</b></div>
        <div class="metric"><small>路上时间</small><b>${esc(plan.summary.total_duration_text)}</b></div>
        <div class="metric"><small>地点数</small><b>${esc(plan.summary.stop_count)} 站</b></div>
      </div>
      ${fullRoute ? `<div class="route-action"><a href="${attr(fullRoute)}" target="_blank" rel="noopener">打开整条高德路线</a></div>` : ""}
    </header>

    <section class="section">
      <h2 class="section-title">时间行程</h2>
      <div class="timeline">${renderStops(plan)}</div>
    </section>

    ${renderSegments(plan)}

    <footer>生成时间：${esc(generatedAt)} · 本文件可在手机或电脑浏览器中离线查看，导航链接需要联网打开。</footer>
  </main>
</body>
</html>`;
}

export function downloadItineraryHtml(plan: Plan): void {
  const html = itineraryHtml(plan);
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const date = new Date().toISOString().slice(0, 10);
  a.href = url;
  a.download = `${slug(plan.city)}-RoamMind行程-${date}.html`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
