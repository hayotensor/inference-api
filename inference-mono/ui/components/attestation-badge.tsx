"use client";

import { ArrowRight, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import Link from "next/link";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from "@/components/ui/dialog";
import { formatDateTime } from "@/lib/format";
import { useAttestation } from "@/lib/attestation";
import { cn } from "@/lib/utils";
import { getStatusVariant, statusStyles } from "@/lib/status";

// A stored "verified" result older than this is shown as stale rather than
// green -- e.g. the tab has been open a long time and the interval was
// throttled/suspended by the browser in a background tab.
const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

type DisplayStatus = "checking" | "verified" | "unverified" | "stale";

// Copy and icon shape supplement the shared semantic status accents.
const DISPLAY: Record<
  DisplayStatus,
  {
    label: string;
    icon: typeof ShieldCheck;
  }
> = {
  checking: {
    label: "Checking attestation",
    icon: ShieldQuestion
  },
  verified: {
    label: "Provably attested",
    icon: ShieldCheck
  },
  unverified: {
    label: "Attestation unverified",
    icon: ShieldAlert
  },
  stale: {
    label: "Attestation check stale",
    icon: ShieldAlert
  }
};

// `reason` is a stable machine code from app/api/attestation-status/route.ts.
// Translate each one into a sentence someone who isn't reading this repo's
// source can actually act on or understand.
const REASON_COPY: Record<string, string> = {
  local_demo: "Attestation is not checked in the local UI demo.",
  pin_not_configured:
    "This deployment has no PLATFORM_PUBKEY configured, so there is nothing independent to check the inference API's claimed signing key against. Set PLATFORM_PUBKEY to the same value pinned by subnet validators and TEEs -- see .env.example.",
  fetch_failed:
    "The inference API's /attest or /allowlist endpoint didn't respond. This usually means the serving plane is unreachable or still starting up -- it does not by itself mean anything is wrong with the platform's attestation.",
  malformed_response:
    "The inference API responded, but /attest or /allowlist was missing fields this check expects (a platform public key, or a list of allowlist entries).",
  key_mismatch:
    "The inference API's claimed signing key does not match this deployment's pinned PLATFORM_PUBKEY. Either the platform key was legitimately rotated and this deployment's pin is stale, or the inference API is misconfigured or compromised -- this needs an operator to check, not a retry.",
  signature_mismatch:
    "The allowlist's signature did not verify against the pinned public key. This is the one result that would genuinely indicate a problem -- either a bug in signing/verification, or the response was tampered with in transit.",
  error: "The verification check itself hit an unexpected error while running."
};

function reasonSentence(reason: string | undefined): string {
  if (!reason) return "No reason was reported.";
  return REASON_COPY[reason] ?? `Reported reason code: "${reason}".`;
}

/**
 * Periodic, independently-verifiable "provably attested" status (HYP-37).
 *
 * This does NOT just read an `attested` flag the API hands back -- it calls
 * /api/attestation-status, which independently re-verifies the platform's
 * signed model allowlist against PLATFORM_PUBKEY (the same key subnet
 * validators and TEEs pin -- never the key the API itself claims) in a
 * from-scratch implementation (lib/attestation-verify.ts), on a schedule, so
 * someone who would never run that verification themselves still sees it
 * happen.
 *
 * Clicking the badge opens the actual detail -- what was checked, why it
 * came out that way, and when -- instead of relying on a native `title`
 * hover tooltip, which is easy to miss and unusable on touch devices.
 */
export function AttestationBadge() {
  const { data: result } = useAttestation();
  const checkedAt = result ? new Date(result.checkedAt) : null;
  const status: DisplayStatus = !result ? "checking" : result.status === "verified" ? "verified" : "unverified";

  const effective: DisplayStatus =
    status === "verified" &&
    checkedAt &&
    Date.now() - checkedAt.getTime() > STALE_AFTER_MS
      ? "stale"
      : status;

  const { label, icon: Icon } = DISPLAY[effective];
  const styles = statusStyles[getStatusVariant(effective)];

  // `allowlistVersion` is not an opaque counter -- inference_api's
  // ModelAllowlistService derives it as the unix timestamp of the newest
  // `updated_at` across the active allowlist rows (see allowlist_service.py),
  // so it IS the real moment the attested content was last (re)proven. That's
  // what a user asking "when was this actually proven" wants -- distinct from
  // `checkedAt`, which is just when this browser last polled and legitimately
  // ticks forward on every reload/re-check even when nothing changed.
  const provenAt =
    effective === "verified" && result?.allowlistVersion
      ? new Date(result.allowlistVersion * 1000)
      : null;

  const summary =
    effective === "verified"
      ? `Independently verified this platform's signed model allowlist ` +
        `(${result?.entryCount ?? 0} model${result?.entryCount === 1 ? "" : "s"}) ` +
        `against its pinned Ed25519 key. The signature checks out.`
      : effective === "stale"
        ? "The last successful check was over 24 hours ago. This page may have been open a long time, or the periodic re-check was paused (e.g. a backgrounded browser tab). Click \"Check now\" for a fresh result."
        : `Could not independently verify the platform's signed allowlist. ${reasonSentence(result?.reason)}`;

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex w-full items-center gap-2 rounded-sm border px-3 py-2 text-left transition-colors duration-fast ease-standard hover:bg-elevated focus-ring",
            styles.badge
          )}
        >
          <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} aria-hidden="true" />
          <span className="min-w-0 truncate text-[11px] font-medium uppercase tracking-label">
            {label}
          </span>
          {provenAt ? (
            <span className="ml-auto shrink-0 font-mono text-[10px] text-stone">
              {provenAt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
            </span>
          ) : checkedAt ? (
            <span className="ml-auto shrink-0 font-mono text-[10px] text-stone">
              {checkedAt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
            </span>
          ) : null}
        </button>
      </DialogTrigger>
      <DialogContent className="max-w-xl gap-6">
        <DialogHeader className="space-y-3 pr-5">
          <DialogTitle className="flex items-center gap-3">
            <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border", styles.badge)}>
              <Icon className="h-5 w-5" strokeWidth={1.5} aria-hidden="true" />
            </span>
            {label}
          </DialogTitle>
          <DialogDescription>{summary}</DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-5 text-sm leading-relaxed text-muted-foreground">
          <section className="space-y-3">
            <h3 className="text-[11px] font-medium uppercase tracking-label text-foreground">
              How verification works
            </h3>
            <p>This app&apos;s server independently verifies the platform in three steps:</p>
            <ol className="list-decimal space-y-2 pl-5 marker:font-medium marker:text-foreground">
              <li className="pl-1">
                <span className="font-medium text-foreground">Check the signing key.</span>{" "}
                Compare the API&apos;s claimed key against the pinned{" "}
                <code className="rounded-sm bg-background px-1 py-0.5 font-mono text-xs text-foreground">PLATFORM_PUBKEY</code>,
                the same key pinned by subnet validators and TEEs.
              </li>
              <li className="pl-1">
                <span className="font-medium text-foreground">Fetch the model allowlist.</span>{" "}
                Retrieve the signed list from{" "}
                <code className="rounded-sm bg-background px-1 py-0.5 font-mono text-xs text-foreground">GET /allowlist</code>.
              </li>
              <li className="pl-1">
                <span className="font-medium text-foreground">Verify the signature.</span>{" "}
                Independently recompute and check the Ed25519 signature.
              </li>
            </ol>
          </section>
          {checkedAt || result?.platformPubkey ? (
            <dl className="grid min-w-0 gap-4 rounded-lg border border-border bg-background/50 p-4 sm:grid-cols-2">
              {provenAt ? (
                <div className="space-y-1">
                  <dt className="text-xs font-medium text-foreground">Proven at</dt>
                  <dd className="space-y-1">
                    <time dateTime={provenAt.toISOString()} className="block text-xs tabular-nums">
                      {formatDateTime(provenAt.toISOString())}
                    </time>
                    <p className="text-xs leading-relaxed">When the current allowlist content was last signed.</p>
                  </dd>
                </div>
              ) : null}
              {checkedAt ? (
                <div className="space-y-1">
                  <dt className="text-xs font-medium text-foreground">Last checked</dt>
                  <dd>
                    <time dateTime={checkedAt.toISOString()} className="text-xs tabular-nums">
                      {formatDateTime(checkedAt.toISOString())}
                    </time>
                  </dd>
                </div>
              ) : null}
              {result?.platformPubkey ? (
                <div className="min-w-0 space-y-1.5 sm:col-span-2">
                  <dt className="text-xs font-medium text-foreground">Pinned platform key</dt>
                  <dd>
                    <code className="block break-all font-mono text-xs leading-relaxed">{result.platformPubkey}</code>
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
          {result?.entries && result.entries.length > 0 ? (
            <section className="min-w-0 space-y-3">
              <h3 className="text-[11px] font-medium uppercase tracking-label text-foreground">Attested models</h3>
              <ul className="space-y-3">
                {result.entries.map((entry, i) => (
                  <li key={`${entry.model_id}-${entry.model_version ?? ""}-${i}`} className="min-w-0 space-y-3 rounded-lg border border-border p-3">
                    <code className="block break-all font-mono text-xs font-medium text-foreground">
                      {entry.model_id}
                      {entry.model_version ? `@${entry.model_version}` : ""}
                    </code>
                    <dl className="space-y-2 text-xs">
                      <div className="grid gap-1 sm:grid-cols-[5rem_minmax(0,1fr)] sm:gap-3">
                        <dt className="font-medium">Model hash</dt>
                        <dd><code className="break-all font-mono leading-relaxed">{entry.model_hash}</code></dd>
                      </div>
                      {entry.gpu_hash ? (
                        <div className="grid gap-1 sm:grid-cols-[5rem_minmax(0,1fr)] sm:gap-3">
                          <dt className="font-medium">GPU hash</dt>
                          <dd><code className="break-all font-mono leading-relaxed">{entry.gpu_hash}</code></dd>
                        </div>
                      ) : null}
                    </dl>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <p className="text-xs">Automatically re-checks every 6 hours while this page is open.</p>
        </div>
        <section className="space-y-3 border-t border-border pt-5">
          <h3 className="text-[11px] font-medium uppercase tracking-label text-foreground">Learn more</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Link
              href="/security"
              className="focus-ring rounded-lg border border-border bg-background/50 p-3 transition-colors duration-fast ease-standard hover:bg-card"
            >
              <span className="flex items-center justify-between gap-2 text-sm font-medium text-foreground">
                <span className="underline underline-offset-4">Security overview</span>
                <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              </span>
              <span className="mt-2 block text-xs leading-relaxed text-muted-foreground">
                How privacy and remote attestation work on the platform.
              </span>
            </Link>
            <a
              href="#"
              className="focus-ring rounded-lg border border-border bg-background/50 p-3 transition-colors duration-fast ease-standard hover:bg-card"
            >
              <span className="flex items-center justify-between gap-2 text-sm font-medium text-foreground">
                <span className="underline underline-offset-4">API documentation</span>
                <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              </span>
              <span className="mt-2 block text-xs leading-relaxed text-muted-foreground">
                How to perform remote attestation yourself.
              </span>
            </a>
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
