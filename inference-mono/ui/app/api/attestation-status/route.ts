import { NextResponse } from "next/server";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

import {
  canonicalAllowlistMessage,
  verifyEd25519Hex,
} from "@/lib/attestation-verify";
import type { AttestationStatusResponse } from "@/lib/types";

// Server-side only: Node's crypto.verify() for Ed25519 keys isn't available in
// the Edge runtime.
export const runtime = "nodejs";
// Always live -- this is a trust check, never served from a cache.
export const dynamic = "force-dynamic";

// Same INTERNAL_*_BASE_URL / NEXT_PUBLIC_*_BASE_URL fallback pattern as
// app/api/sso-cookie/route.ts.
const INFERENCE_API_BASE_URL =
  process.env.INTERNAL_INFERENCE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_INFERENCE_API_BASE_URL ??
  "http://localhost:8001";

// The actual trust anchor. This is NOT re-derived from the inference-api's own
// `GET /attest` response -- that response is a claim from the party being
// checked, and trusting it would only prove the inference-api is internally
// self-consistent, not that it's honest or uncompromised. It's the SAME
// platform provisioner verify key every subnet validator pins via
// `--allowlist_signer_pubkey` (inference-subnet/inference/cli/run_node.py)
// and every TEE pins via `PROVISIONER_VERIFY_KEY_HEX`
// (inference-subnet-miner/docs/deployment.md) -- one value, configured
// identically everywhere it's checked. Per `/attest`'s own docstring: an
// operator fetches it ONCE at setup time and pins it out of band (here, as
// deploy config); never copy it into source and never accept an artifact's
// self-reported key as a substitute.
const PINNED_PLATFORM_PUBKEY = process.env.PLATFORM_PUBKEY?.trim().toLowerCase() || undefined;

/**
 * GET /api/attestation-status (HYP-37): the frontend's own, from-scratch
 * verification of the platform's signed model allowlist -- NOT a proxy of the
 * inference-api's own claims. Fetches the signed artifact from `GET
 * /allowlist`, checks the inference-api's claimed signer (`GET /attest`)
 * against the PINNED_PLATFORM_PUBKEY configured for this deployment (the same
 * value validators and TEEs pin -- see above), and only if that matches does
 * it independently recompute the canonical signed bytes and check the
 * signature in this server process (see lib/attestation-verify.ts). A caller
 * who does not trust the inference-api at all still gets an honest answer,
 * because the trust anchor never comes from the inference-api itself.
 */
export async function GET() {
  const checkedAt = new Date().toISOString();

  if (UI_DEMO_MODE) {
    return NextResponse.json<AttestationStatusResponse>({ status: "unverified", checkedAt, reason: "local_demo" });
  }

  if (!PINNED_PLATFORM_PUBKEY) {
    // Fail closed: with no independently configured pin, there is nothing to
    // check the inference-api's claimed key against, so a "verified" result
    // would be meaningless. See PLATFORM_PUBKEY in .env.example.
    return NextResponse.json<AttestationStatusResponse>({
      status: "unverified",
      checkedAt,
      reason: "pin_not_configured",
    });
  }

  try {
    const [attestRes, allowlistRes] = await Promise.all([
      fetch(`${INFERENCE_API_BASE_URL}/attest`, { cache: "no-store" }),
      fetch(`${INFERENCE_API_BASE_URL}/allowlist`, { cache: "no-store" }),
    ]);
    if (!attestRes.ok || !allowlistRes.ok) {
      return NextResponse.json<AttestationStatusResponse>({
        status: "unverified",
        checkedAt,
        reason: "fetch_failed",
      });
    }
    const attest = await attestRes.json();
    const allowlist = await allowlistRes.json();
    const claimedPubkey: unknown = attest?.platform_pubkey;
    if (typeof claimedPubkey !== "string" || !Array.isArray(allowlist?.entries)) {
      return NextResponse.json<AttestationStatusResponse>({
        status: "unverified",
        checkedAt,
        reason: "malformed_response",
      });
    }
    // The inference-api's claim of its own key is never trusted by itself --
    // it must match the independently pinned value. A mismatch means either
    // this deployment's pin is stale (the platform key was legitimately
    // rotated) or the inference-api is misconfigured/compromised; either way,
    // falling back to "whatever key it just told us" would defeat the point.
    if (claimedPubkey.trim().toLowerCase() !== PINNED_PLATFORM_PUBKEY) {
      return NextResponse.json<AttestationStatusResponse>({
        status: "unverified",
        checkedAt,
        reason: "key_mismatch",
      });
    }
    const message = canonicalAllowlistMessage(
      allowlist.entries,
      allowlist.version,
      allowlist.not_before ?? null
    );
    const ok = verifyEd25519Hex(PINNED_PLATFORM_PUBKEY, message, String(allowlist.signature ?? ""));
    return NextResponse.json<AttestationStatusResponse>({
      status: ok ? "verified" : "unverified",
      checkedAt,
      reason: ok ? undefined : "signature_mismatch",
      platformPubkey: PINNED_PLATFORM_PUBKEY,
      allowlistVersion: allowlist.version,
      entryCount: allowlist.entries.length,
      // Only surface the actual claims once the signature checks out against
      // the pinned key -- never render unverified entries as if attested.
      entries: ok ? allowlist.entries : undefined,
    });
  } catch {
    return NextResponse.json<AttestationStatusResponse>({
      status: "unverified",
      checkedAt,
      reason: "error",
    });
  }
}
