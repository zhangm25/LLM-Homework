// Mirrors backend/app/models/plan.py. Keep in sync with the backend contract.

export interface UnderstandStep {
  index: string;
  label: string;
  locked: boolean;
}

export interface Understanding {
  kind: "mood" | "explicit";
  title: string;
  mood_chips: string[];
  want_chips: string[];
  avoid_chips: string[];
  steps: UnderstandStep[];
  constraints: string[];
}

export interface POIChoice {
  name: string;
  location: [number, number]; // [lng, lat]
  rating: number | null;
  cost: string | null;
  address: string | null;
}

export interface Stop {
  kind: "start" | "poi" | "end" | "fixed";
  marker: string;
  time: string;
  name: string;
  tags: string[];
  rating: number | null;
  cost: string | null;
  why: string | null;
  leg: string | null;
  location: [number, number] | null; // [lng, lat]
  open_info: string | null;
  dwell_min: number;
  alternatives: POIChoice[];
}

export interface RouteSummary {
  total_distance_text: string;
  total_duration_text: string;
  stop_count: number;
  polyline: [number, number][];
  segments: RouteSegment[];
}

export interface RouteSegment {
  from_index: number;
  to_index: number;
  from_name: string;
  to_name: string;
  mode: "walking" | "driving" | "transit" | "auto";
  polyline: [number, number][];
}

export interface NavLinks {
  app_uri_android: string;
  app_uri_ios: string;
  web_uri: string;
  waypoint_count: number;
  waypoint_names: string[];
  web_supports_all_waypoints: boolean;
  segment_web_uris: string[];
}

export interface Feasibility {
  ok: boolean;
  note: string;
}

export interface Plan {
  city: string;
  panel_hint: string;
  understanding: Understanding | null;
  summary: RouteSummary;
  timeline: Stop[];
  nav: NavLinks;
  feasibility: Feasibility;
  intent?: unknown | null;
  place_resolution?: PlaceResolution | null;
  source: "mock" | "live";
}

export interface PlaceCandidate {
  id: string;
  name: string;
  address: string;
  location: [number, number];
  rating: number | null;
  cost: string | null;
  distance_m: number | null;
  type: string;
}

export interface PlaceSlot {
  id: string;
  role: "start" | "end" | "fixed" | "waypoint" | "activity_poi";
  source_text: string;
  query: string;
  city: string;
  anchor_label: string | null;
  anchor_location: [number, number] | null;
  status: "selected" | "candidates_ready" | "unresolved" | "skipped";
  candidates: PlaceCandidate[];
  selected: PlaceCandidate | null;
  needs_user_choice: boolean;
  reason: string;
}

export interface PlaceResolution {
  status: "places_ready" | "partial" | "unresolved";
  slots: PlaceSlot[];
}

export interface ClarifyOption {
  id: string;
  label: string;
  description: string;
  message: string;
}

export type StreamEvent =
  | { type: "thinking"; text: string }
  | { type: "understanding"; understanding: Understanding }
  | { type: "clarify"; text: string; options: ClarifyOption[]; intent?: unknown | null }
  | { type: "message"; text: string }
  | { type: "plan"; plan: Plan }
  | { type: "done" }
  | { type: "error"; text: string };

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequestBody {
  message: string;
  history: ChatMessage[];
  city?: string | null;
  origin?: { lng: number; lat: number; label?: string } | null;
  origin_status?: "available" | "unknown" | "denied" | "unsupported" | "unavailable" | "timeout";
  scenario?: string | null;
  intent?: unknown | null;
}

export interface AppConfig {
  llm_enabled: boolean;
  llm?: {
    configured: boolean;
    ok: boolean;
    provider: string;
    model: string;
    base_url: string;
    message: string;
  };
  amap_web_enabled: boolean;
  default_city: string;
  scenarios: string[];
}

// Items rendered in the chat stream (user/bot bubbles, understanding cards,
// and the final feasibility status line).
export type ChatItem =
  | { id: string; kind: "msg"; role: "user" | "assistant"; content: string }
  | { id: string; kind: "understanding"; data: Understanding }
  | { id: string; kind: "clarify"; text: string; options: ClarifyOption[] }
  | { id: string; kind: "status"; text: string };
