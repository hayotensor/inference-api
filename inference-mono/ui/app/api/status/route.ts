import { NextResponse } from "next/server";

import { buildStatusPage, ensureSampler } from "@/lib/server/status-history";

export const runtime = "nodejs";
// A status page should never serve a stale cached snapshot.
export const dynamic = "force-dynamic";

/**
 * GET /api/status (HYP-58): the public status page's data. The background
 * sampler in `lib/server/status-history.ts` probes every component each minute
 * (landing page, inference API, chat, miner network, each model) and keeps
 * 90 days of uptime history plus incidents; this route just starts it
 * (idempotent) and returns the rollup.
 */
export async function GET() {
  await ensureSampler();
  return NextResponse.json(buildStatusPage());
}
