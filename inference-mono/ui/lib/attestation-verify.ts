import { createPublicKey, verify as cryptoVerify } from "node:crypto";

/**
 * Independent, from-scratch re-implementation of the inference-api's signed
 * artifact verification (HYP-25 / HYP-38 "provability"). This deliberately
 * does NOT trust the inference-api's own `"attested": true` / `"verified"`
 * claims -- it re-derives the exact bytes the platform's Ed25519 key signed
 * (mirroring `talaris_contracts.allowlist.canonical_allowlist_message`
 * byte-for-byte: compact JSON, recursively sorted keys) and checks the
 * signature itself, in this server process, using nothing but Node's builtin
 * `crypto` module (no dependency on the inference-api's own code).
 *
 * Server-side only (Node's `crypto.verify` for Ed25519 keys is not available
 * in the Edge runtime) -- see app/api/attestation-status/route.ts, which is
 * the only caller.
 */

/** Canonical JSON: compact separators, keys sorted recursively at every level. */
export function canonicalJsonStringify(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJsonStringify).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const parts = keys.map(
    (key) => `${JSON.stringify(key)}:${canonicalJsonStringify(record[key])}`
  );
  return `{${parts.join(",")}}`;
}

export interface AllowlistEntryWire {
  model_id: string;
  model_version?: string | null;
  model_hash: string;
  args_hash?: string | null;
  gpu_hash?: string | null;
  label?: string | null;
}

/** Mirrors talaris_contracts.allowlist._entry_fields exactly. */
function normalizeEntry(entry: AllowlistEntryWire) {
  return {
    model_id: String(entry.model_id ?? ""),
    model_version: String(entry.model_version ?? ""),
    model_hash: String(entry.model_hash ?? "").toLowerCase(),
    args_hash: String(entry.args_hash ?? ""),
    gpu_hash: String(entry.gpu_hash ?? ""),
    label: String(entry.label ?? ""),
  };
}

/**
 * Byte-for-byte port of
 * `talaris_contracts.allowlist.canonical_allowlist_message`: normalizes and
 * sorts entries by (model_id, model_version, model_hash), then serializes
 * `{kind, version, entries[, not_before]}` as compact, key-sorted JSON.
 */
export function canonicalAllowlistMessage(
  entries: AllowlistEntryWire[],
  version: number,
  notBefore: number | null | undefined
): string {
  const normalized = entries.map(normalizeEntry);
  // Tuple comparison on (model_id, model_version, model_hash), matching the
  // Python side's `key=lambda d: (d["model_id"], d["model_version"], d["model_hash"])`.
  normalized.sort((a, b) => {
    const fields = ["model_id", "model_version", "model_hash"] as const;
    for (const field of fields) {
      if (a[field] < b[field]) return -1;
      if (a[field] > b[field]) return 1;
    }
    return 0;
  });
  const body: Record<string, unknown> = {
    kind: "model-allowlist/v1",
    version: Math.trunc(version),
    entries: normalized,
  };
  if (notBefore !== null && notBefore !== undefined) {
    body.not_before = Math.trunc(notBefore);
  }
  return canonicalJsonStringify(body);
}

/** Verify a hex Ed25519 signature over `message` with a hex raw public key. */
export function verifyEd25519Hex(
  publicKeyHex: string,
  message: string,
  signatureHex: string
): boolean {
  try {
    const rawKey = Buffer.from(publicKeyHex, "hex");
    if (rawKey.length !== 32) return false;
    const publicKey = createPublicKey({
      key: { kty: "OKP", crv: "Ed25519", x: rawKey.toString("base64url") },
      format: "jwk",
    });
    const signature = Buffer.from(signatureHex, "hex");
    return cryptoVerify(null, Buffer.from(message, "utf8"), publicKey, signature);
  } catch {
    return false;
  }
}
