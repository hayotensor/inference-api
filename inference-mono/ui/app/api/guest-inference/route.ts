import { NextResponse } from "next/server";

// Same INTERNAL_*_BASE_URL / NEXT_PUBLIC_*_BASE_URL fallback pattern as
// app/api/status/route.ts / app/api/attestation-status/route.ts -- the
// inference-api may only be reachable from this Next.js server (internal
// network), not from the browser directly (see talaris-edge/worker, whose
// public routing only knows about the dashboard and chat origins), so the
// homepage's free sandbox (HYP-67) is proxied here rather than the client
// fetching NEXT_PUBLIC_INFERENCE_API_BASE_URL itself.
const INFERENCE_API_BASE_URL =
  process.env.INTERNAL_INFERENCE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_INFERENCE_API_BASE_URL ??
  "http://localhost:8001";

export const runtime = "nodejs";
// Never let a CDN/browser cache a sandbox reply for a different visitor.
export const dynamic = "force-dynamic";

const MAX_PROMPT_LENGTH = 20_000;

function jsonError(status: number, code: string, message: string) {
  return NextResponse.json({ detail: { code, message } }, { status });
}

/**
 * Best-effort REAL visitor IP, forwarded to the inference-api so its per-IP
 * daily ceiling (inference_api.guest_limits) buckets by the actual visitor
 * rather than by this Next.js server's own address.
 *
 * Prefers Cloudflare's own tamper-resistant `CF-Connecting-IP` (this stack
 * sits behind a Cloudflare Worker + Tunnel in production -- see
 * talaris-edge/worker/src/index.js), falling back to the left-most
 * X-Forwarded-For entry from whatever is in front locally. If neither is
 * present (e.g. bare local dev, nothing in front of this Next.js server),
 * this returns null and the inference-api falls back to ITS OWN peer address
 * (this Next.js server) -- correct for local dev (one shared bucket while
 * testing), but see the HYP-67 report note on why TRUSTED_PROXY_IPS must
 * still be configured on the inference-api for this forwarded value to be
 * honored at all in any environment where this route is used.
 */
function resolveVisitorIp(request: Request): string | null {
  const cfIp = request.headers.get("cf-connecting-ip");
  if (cfIp?.trim()) return cfIp.trim();
  const forwardedFor = request.headers.get("x-forwarded-for");
  const first = forwardedFor?.split(",")[0]?.trim();
  return first || null;
}

/**
 * POST /api/guest-inference (HYP-67): proxies the inference-api's
 * unauthenticated `POST /v1/guest/inference` for the homepage's free sandbox.
 * Thin passthrough of both the request (prompt + X-Guest-Id) and the
 * response (status + body) -- all rate-limit/model/token-cap enforcement
 * happens server-side on the inference-api, never here.
 */
export async function POST(request: Request) {
  const guestId = request.headers.get("x-guest-id");
  if (!guestId) {
    return jsonError(
      400,
      "guest_id_required",
      "X-Guest-Id header is required (a per-browser id generated once and stored in localStorage)."
    );
  }

  let prompt: unknown;
  try {
    const body = await request.json();
    prompt = (body as { prompt?: unknown } | null)?.prompt;
  } catch {
    return jsonError(400, "invalid_request", "Request body must be JSON.");
  }
  if (typeof prompt !== "string" || !prompt.trim() || prompt.length > MAX_PROMPT_LENGTH) {
    return jsonError(400, "invalid_request", "A non-empty `prompt` is required.");
  }

  const visitorIp = resolveVisitorIp(request);
  const upstreamHeaders: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Guest-Id": guestId
  };
  if (visitorIp) upstreamHeaders["X-Forwarded-For"] = visitorIp;

  let upstream: Response;
  try {
    upstream = await fetch(`${INFERENCE_API_BASE_URL}/v1/guest/inference`, {
      method: "POST",
      headers: upstreamHeaders,
      body: JSON.stringify({ prompt }),
      cache: "no-store"
    });
  } catch {
    return jsonError(
      502,
      "guest_inference_unreachable",
      "The free sandbox is temporarily unavailable."
    );
  }

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" }
  });
}
