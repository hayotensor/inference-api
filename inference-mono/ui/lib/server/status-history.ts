import { promises as fs } from "node:fs";
import path from "node:path";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

import type {
  ModelServiceStatus,
  StatusComponent,
  StatusGroup,
  StatusHistoryDay,
  StatusIncident,
  StatusPageResponse,
  StatusResponse
} from "@/lib/types";

// HYP-58: status-page backend. The inference-api only exposes a live snapshot
// (`GET /status/models`), so this Next.js server runs a background sampler
// every minute that probes each component (landing page, API, chat, the miner
// network, and every model), rolls samples into hourly buckets, tracks
// incidents (a contiguous run of non-operational samples), and persists it all
// to STATUS_HISTORY_FILE (default ./.status-history.json) so history survives
// restarts. History starts when the sampler first ran; earlier days read as
// "no data".

const SAMPLE_INTERVAL_MS = 60_000;
const RETENTION_DAYS = 90;
const SLOW_MS = 3_000;
const HISTORY_FILE =
  process.env.STATUS_HISTORY_FILE ?? path.join(process.cwd(), ".status-history.json");

const API_BASE =
  process.env.INTERNAL_INFERENCE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_INFERENCE_API_BASE_URL ??
  "http://localhost:8001";
const SELF_URL = process.env.STATUS_SELF_URL ?? `http://127.0.0.1:${process.env.PORT ?? "3000"}`;
const CHAT_URL = (() => {
  const configured = process.env.STATUS_CHAT_URL ?? process.env.NEXT_PUBLIC_CHAT_URL ?? "/chat";
  return /^https?:\/\//.test(configured) ? configured : `${SELF_URL}${configured}`;
})();

type Bucket = {
  samples: number;
  operational: number;
  degraded: number;
  down: number;
  latencySum: number;
  latencyN: number;
};
type Meta = { name: string; group: string; description?: string };
type Store = {
  buckets: Record<string, Record<string, Bucket>>; // hour -> component -> counts
  meta: Record<string, Meta>;
  incidents: StatusIncident[];
};

const GROUPS = ["Core services", "Providers", "Models"] as const;
const SEVERITY: Record<ModelServiceStatus, number> = { operational: 0, degraded: 1, down: 2 };

const state: { store: Store; loaded: boolean; timer: NodeJS.Timeout | null; saving: boolean } = {
  store: { buckets: {}, meta: {}, incidents: [] },
  loaded: false,
  timer: null,
  saving: false
};
const live: Record<string, ModelServiceStatus> = {};

async function load() {
  if (state.loaded) return;
  state.loaded = true;
  try {
    const parsed = JSON.parse(await fs.readFile(HISTORY_FILE, "utf8")) as Store;
    if (parsed.buckets && parsed.meta && parsed.incidents) state.store = parsed;
  } catch {
    // first run
  }
}

async function save() {
  if (state.saving) return;
  state.saving = true;
  try {
    await fs.writeFile(HISTORY_FILE, JSON.stringify(state.store));
  } catch {
    // Read-only filesystem: history stays in memory only.
  } finally {
    state.saving = false;
  }
}

const hourKey = (date: Date) => date.toISOString().slice(0, 13);

type Observation = { id: string; meta: Meta; status: ModelServiceStatus; latencyMs?: number };

/** Record one probe round. Exported for tests. */
export function recordObservations(observations: Observation[], now = new Date()) {
  const { store } = state;
  const hour = (store.buckets[hourKey(now)] ??= {});
  for (const o of observations) {
    store.meta[o.id] = o.meta;
    live[o.id] = o.status;
    const b = (hour[o.id] ??= {
      samples: 0,
      operational: 0,
      degraded: 0,
      down: 0,
      latencySum: 0,
      latencyN: 0
    });
    b.samples += 1;
    b[o.status] += 1;
    if (o.latencyMs !== undefined) {
      b.latencySum += o.latencyMs;
      b.latencyN += 1;
    }

    // Incident tracking: open on first non-operational sample, escalate to the
    // worst status seen, resolve on the next operational sample.
    const open = store.incidents.find((i) => i.component_id === o.id && i.resolved_at === null);
    if (o.status !== "operational") {
      if (!open) {
        store.incidents.push({
          id: `${o.id}@${now.toISOString()}`,
          component_id: o.id,
          component_name: o.meta.name,
          status: o.status,
          started_at: now.toISOString(),
          resolved_at: null
        });
      } else if (SEVERITY[o.status] > SEVERITY[open.status]) {
        open.status = o.status;
      }
    } else if (open) {
      open.resolved_at = now.toISOString();
    }
  }
  const cutoff = hourKey(new Date(now.getTime() - RETENTION_DAYS * 86_400_000));
  for (const k of Object.keys(store.buckets)) if (k < cutoff) delete store.buckets[k];
  const incidentCutoff = new Date(now.getTime() - RETENTION_DAYS * 86_400_000).toISOString();
  store.incidents = store.incidents.filter((i) => i.started_at >= incidentCutoff);
}

async function probe(url: string, init?: RequestInit) {
  const started = Date.now();
  try {
    const res = await fetch(url, {
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
      ...init
    });
    return { res, latencyMs: Date.now() - started };
  } catch {
    return { res: null, latencyMs: Date.now() - started };
  }
}

function httpStatus(r: { res: Response | null; latencyMs: number }): ModelServiceStatus {
  // 2xx/3xx/4xx all mean the service answered (chat redirects to login);
  // only 5xx or no answer is an outage.
  if (!r.res || r.res.status >= 500) return "down";
  return r.latencyMs > SLOW_MS ? "degraded" : "operational";
}

async function sampleOnce() {
  const observations: Observation[] = [];
  const core = (id: string, name: string, description: string): Meta => ({
    name,
    group: "Core services",
    description
  });

  const [landing, chat, api] = await Promise.all([
    probe(`${SELF_URL}/`),
    probe(CHAT_URL),
    probe(`${API_BASE}/status/models`)
  ]);
  observations.push(
    { id: "landing", meta: core("landing", "Landing page", "Marketing site and sign-up"), status: httpStatus(landing), latencyMs: landing.latencyMs },
    { id: "chat", meta: core("chat", "Chat interface", "Web chat application"), status: httpStatus(chat), latencyMs: chat.latencyMs },
    { id: "api", meta: core("api", "Inference API", "Authentication, billing, and inference routing"), status: api.res?.ok ? (api.latencyMs > SLOW_MS ? "degraded" : "operational") : "down", latencyMs: api.latencyMs }
  );

  if (api.res?.ok) {
    try {
      const snapshot = (await api.res.json()) as StatusResponse;
      const withMiners = snapshot.models.filter((m) => m.registered_miner_count > 0);
      const healthy = withMiners.filter((m) => m.healthy_miner_count > 0).length;
      const providerStatus: ModelServiceStatus =
        withMiners.length === 0 || healthy === 0
          ? "down"
          : healthy < withMiners.length
            ? "degraded"
            : "operational";
      observations.push({
        id: "providers",
        meta: { name: "Miner network", group: "Providers", description: "Attested inference providers" },
        status: providerStatus
      });
      for (const m of snapshot.models) {
        observations.push({
          id: `model:${m.model_id}`,
          meta: { name: m.model_id, group: "Models", description: m.label ?? undefined },
          status: m.status
        });
      }
    } catch {
      // unparseable body: API counts as degraded below via the status above
    }
  }

  recordObservations(observations);
  await save();
}

/** Idempotent: loads persisted history and starts the background sampler. */
export async function ensureSampler() {
  if (UI_DEMO_MODE) return;
  await load();
  if (!state.timer) {
    state.timer = setInterval(() => void sampleOnce(), SAMPLE_INTERVAL_MS);
    state.timer.unref?.();
    void sampleOnce();
  }
}

function worst(b: Bucket): ModelServiceStatus {
  if (b.down > 0) return "down";
  if (b.degraded > 0) return "degraded";
  return "operational";
}

function buildDays(id: string, days: number, now: Date) {
  const series: StatusHistoryDay[] = [];
  let latencySum = 0;
  let latencyN = 0;
  for (let i = days - 1; i >= 0; i--) {
    const day = new Date(now.getTime() - i * 86_400_000).toISOString().slice(0, 10);
    const t: Bucket = { samples: 0, operational: 0, degraded: 0, down: 0, latencySum: 0, latencyN: 0 };
    for (const [key, hour] of Object.entries(state.store.buckets)) {
      const b = key.startsWith(day) ? hour[id] : undefined;
      if (!b) continue;
      for (const k of Object.keys(t) as (keyof Bucket)[]) t[k] += b[k];
    }
    latencySum += t.latencySum;
    latencyN += t.latencyN;
    series.push(
      t.samples === 0
        ? { day, status: null, uptime: null }
        : { day, status: worst(t), uptime: t.operational / t.samples }
    );
  }
  return { series, latencyMs: latencyN ? Math.round(latencySum / latencyN) : null };
}

export function buildStatusPage(days = 90, now = new Date()): StatusPageResponse {
  const groups: StatusGroup[] = GROUPS.map((name) => ({ name, components: [] }));
  for (const [id, meta] of Object.entries(state.store.meta)) {
    const group = groups.find((g) => g.name === meta.group);
    if (!group) continue;
    const { series, latencyMs } = buildDays(id, days, now);
    const measured = series.filter((d) => d.uptime !== null);
    const component: StatusComponent = {
      id,
      name: meta.name,
      description: meta.description ?? null,
      status: live[id] ?? "down",
      uptime: measured.length
        ? measured.reduce((s, d) => s + (d.uptime ?? 0), 0) / measured.length
        : null,
      latency_ms: latencyMs,
      history: series
    };
    group.components.push(component);
  }
  for (const g of groups) g.components.sort((a, b) => a.name.localeCompare(b.name));

  const all = groups.flatMap((g) => g.components);
  const overall = all.reduce<ModelServiceStatus>(
    (acc, c) => (SEVERITY[c.status] > SEVERITY[acc] ? c.status : acc),
    "operational"
  );
  const incidents = [...state.store.incidents]
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
    .slice(0, 50);
  return {
    generated_at: now.toISOString(),
    overall_status: overall,
    groups: groups.filter((g) => g.components.length > 0),
    incidents,
    history_days: days
  };
}
