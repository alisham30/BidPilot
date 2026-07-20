// Thin typed API client — all logic is server-side; the frontend renders state
// and posts decisions.
const BASE = "http://localhost:8000";

export interface Rfp {
  rfp_id: string; title: string; issuer: string; reference_no: string;
  due_date: string | null; due_unknown: boolean; days_left: number | null;
  source: string; source_detail: string; status: string; doc_count: number;
  created_at: string;
}

export interface Evidence { param: string; kind: string; required: string; actual: string | null; score: number; }
export interface MatchResult { sku_id: string; pct: number; evidence: Evidence[]; }
export interface TechItem {
  item_no: string; description: string; quantity: number; unit: string;
  top3: MatchResult[]; top_pick: string | null; below_threshold: boolean;
}
export interface PriceLine {
  item_no: string; sku_id: string; description: string; quantity: number; unit: string;
  unit_price: number; currency: string; amount: number; priced: boolean;
}
export interface TestPriceLine { test_name: string; standard: string; price: number; currency: string; priced: boolean; }
export interface VerdictItem { item_no: string; status: "verified" | "flagged"; reasons: string[]; }

export interface RunState {
  rfp_id?: string;
  product_summary?: string; test_summary?: string;
  tech?: { items: TechItem[] };
  price?: {
    lines: PriceLine[]; test_lines: TestPriceLine[];
    material_total: number; test_total: number; grand_total: number; currency: string;
  };
  verdict?: { per_item: VerdictItem[]; overall: string; evidence: string[] };
  mto?: { item_no: string; closest_sku: string; draft_subject: string; draft_body: string }[];
  run_log?: string[];
}

export interface RunDetail {
  run_id: string; rfp_id: string; status: string;
  started_at: string; finished_at: string | null;
  state: RunState;
  decisions: { actor: string; action: string; payload: Record<string, unknown>; decided_at: string }[];
}

export interface Escalation {
  id: string; rfp_id: string | null; source_agent: string; reason: string;
  severity: "low" | "medium" | "high"; status: string; created_at: string;
}

export interface Followup {
  id: string; rfp_id: string; subject: string; body: string;
  reason: string; status: string; created_at: string;
}

export interface QueueEntry {
  rfp_id: string; run_id: string; title: string; issuer: string;
  due_date: string | null; days_left: number | null; running: boolean;
  verdict: string | null; grand_total: number | null;
  items_total: number; items_flagged: number;
}

export interface Stats {
  rfps_total: number; rfps_awaiting_review: number; rfps_in_window: number;
  bids_submitted: number; open_escalations: number; skus: number;
  review_queue: QueueEntry[];
}

export const inr = (n: number | null | undefined) =>
  n == null ? "—" : "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 0 });

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status}: ${body.slice(0, 300)}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  stats: () => req<Stats>("/stats"),
  rfps: (window?: string) => req<Rfp[]>(`/rfps${window ? `?window=${window}` : ""}`),
  rfp: (id: string) => req<Rfp & { dataset: unknown; runs: { run_id: string; status: string; started_at: string }[]; doc_paths: string[] }>(`/rfps/${id}`),
  respond: (id: string) => req<{ run_id: string }>(`/rfps/${id}/respond`, { method: "POST" }),
  run: (id: string) => req<RunDetail>(`/runs/${id}`),
  decision: (runId: string, actor: string, action: string, payload: Record<string, unknown> = {}) =>
    req(`/runs/${runId}/decision`, { method: "POST", body: JSON.stringify({ actor, action, payload }) }),
  pdfUrl: (runId: string) => `${BASE}/runs/${runId}/pdf`,
  escalations: (status = "open") => req<Escalation[]>(`/escalations?status=${status}`),
  ackEscalation: (id: string, actor: string) =>
    req(`/escalations/${id}/ack`, { method: "POST", body: JSON.stringify({ actor }) }),
  bids: () => req<{ bids: Rfp[]; followups: Followup[] }>("/bids"),
  sendFollowup: (id: string, actor: string) =>
    req(`/followups/${id}/send`, { method: "POST", body: JSON.stringify({ actor }) }),
  scanEmail: () => req("/scan/email", { method: "POST" }),
  scanWeb: () => req("/scan/web", { method: "POST" }),
  rebuildCatalog: () => req("/catalog/rebuild", { method: "POST" }),
  catalogStats: () => req<{ skus: number; service_prices: number; categories: string[] }>("/catalog/stats"),
};

export function runSocket(runId: string, onMessage: (data: { type: string; node?: string; state?: RunState }) => void): WebSocket {
  const ws = new WebSocket(`${BASE.replace("http", "ws")}/ws/runs/${runId}`);
  ws.onmessage = (ev) => {
    try { onMessage(JSON.parse(ev.data)); } catch { /* ignore */ }
  };
  const ping = setInterval(() => { if (ws.readyState === ws.OPEN) ws.send("ping"); }, 25000);
  ws.onclose = () => clearInterval(ping);
  return ws;
}
