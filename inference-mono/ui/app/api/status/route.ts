import { NextResponse } from "next/server";

import type { StatusResponse } from "@/lib/types";

// Same INTERNAL_*_BASE_URL / NEXT_PUBLIC_*_BASE_URL fallback pattern as
// app/api/attestation-status/route.ts -- the inference-api may only be
// reachable from this Next.js server (internal network), not from the
// browser directly, so this route proxies it server-side rather than the
// status page fetching NEXT_PUBLIC_INFERENCE_API_BASE_URL itself.
const INFERENCE_API_BASE_URL =
  process.env.INTERNAL_INFERENCE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_INFERENCE_API_BASE_URL ??
  "http://localhost:8001";

export const runtime = "nodejs";
// A status page should never serve a stale cached snapshot.
export const dynamic = "force-dynamic";

/**
 * GET /api/status (HYP-58): proxies the inference-api's public, unauthenticated
 * `GET /status/models` for the dashboard's `/status` page. Thin passthrough --
 * no re-verification step here (unlike attestation-status), since this is
 * plain operational telemetry, not a signed trust claim.
 */
export async function GET() {
  try {
    const res = await fetch(`${INFERENCE_API_BASE_URL}/status/models`, { cache: "no-store" });
    if (!res.ok) {
      return NextResponse.json({ error: "status_fetch_failed" }, { status: 502 });
    }
    const payload = (await res.json()) as StatusResponse;
    return NextResponse.json(payload);
  } catch {
    return NextResponse.json({ error: "status_fetch_error" }, { status: 502 });
  }
}
