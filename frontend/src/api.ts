import type { AppConfig, ChatRequestBody, Plan, StreamEvent, Stop } from "./types";

const ENV_BASE = import.meta.env.VITE_API_BASE_URL?.trim();
// Default to SAME-ORIGIN (relative) requests, so the app never depends on a
// second port being reachable from the browser:
//   * `npm run dev`  -> page is :5173, Vite proxies /api to the backend.
//   * FastAPI serving frontend/dist -> page and /api are both on :8000.
// Both "just work" over an SSH tunnel or a direct IP. Set VITE_API_BASE_URL
// only if the backend lives on a different origin you must hit directly.
export const API_BASE = ENV_BASE && ENV_BASE.length > 0 ? ENV_BASE : "";

export async function getConfig(): Promise<AppConfig | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/config`);
    if (!resp.ok) return null;
    return (await resp.json()) as AppConfig;
  } catch {
    return null;
  }
}

export async function reverseGeocode(lng: number, lat: number): Promise<string | null> {
  try {
    const params = new URLSearchParams({ lng: String(lng), lat: String(lat) });
    const resp = await fetch(`${API_BASE}/api/regeo?${params.toString()}`);
    if (!resp.ok) return null;
    const data = (await resp.json()) as { address?: string | null };
    return data.address ?? null;
  } catch {
    return null;
  }
}

/** Swap a stop to an alternative; the server re-routes deterministically. */
export async function recomputeRoute(
  timeline: Stop[],
  city: string,
  intent: unknown,
): Promise<Plan | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/route`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timeline, city, intent }),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as Plan;
  } catch {
    return null;
  }
}

export interface PlaceCandidate {
  lng: number;
  lat: number;
  label: string;
  address?: string;
}

/** Candidate start locations for the picker dropdown (user chooses one). */
export async function suggestPlaces(q: string, city?: string | null): Promise<PlaceCandidate[]> {
  try {
    const params = new URLSearchParams({ q });
    if (city) params.set("city", city);
    const resp = await fetch(`${API_BASE}/api/places?${params.toString()}`);
    if (!resp.ok) return [];
    const data = (await resp.json()) as { candidates?: PlaceCandidate[] };
    return data.candidates ?? [];
  } catch {
    return [];
  }
}

export async function geocodePlace(
  q: string,
  city?: string | null,
): Promise<{ lng: number; lat: number; label: string; address?: string } | null> {
  try {
    const params = new URLSearchParams({ q });
    if (city) params.set("city", city);
    const resp = await fetch(`${API_BASE}/api/geocode?${params.toString()}`);
    if (!resp.ok) return null;
    const data = (await resp.json()) as {
      found?: boolean;
      lng?: number;
      lat?: number;
      label?: string;
      address?: string;
    };
    if (!data.found || data.lng == null || data.lat == null || !data.label) return null;
    return { lng: data.lng, lat: data.lat, label: data.label, address: data.address };
  } catch {
    return null;
  }
}

/**
 * POST to /api/chat and parse the SSE stream (`data: <json>\n\n` per event),
 * invoking `onEvent` for each parsed StreamEvent. Resolves when the stream ends.
 */
export async function streamChat(
  body: ChatRequestBody,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`后端请求失败：HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      try {
        onEvent(JSON.parse(json) as StreamEvent);
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}
