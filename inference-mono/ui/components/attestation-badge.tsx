"use client";

import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";

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
import { Button } from "@/components/ui/button";

// A stored "verified" result older than this is shown as stale rather than
// green -- e.g. the tab has been open a long time and the interval was
// throttled/suspended by the browser in a background tab.
const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

type DisplayStatus = "checking" | "verified" | "unverified" | "stale";

// The Talaris system has no green/amber/red to signal these four states, so
// they are distinguished by tone and border weight instead, per the kit:
// a verified check is the only one at full Off-White over a Stone Gray border;
// the two problem states keep the Off-White glyph but sit on the Graphite
// border; "checking" recedes entirely to Warm Gray. See ui/badge.tsx for the
// same weight ladder applied to tags.
const DISPLAY: Record<
  DisplayStatus,
  {
    label: string;
    icon: typeof ShieldCheck;
    className: string;
    borderClassName: string;
  }
> = {
  checking: {
    label: "Checking attestation",
    icon: ShieldQuestion,
    className: "text-muted-foreground",
    borderClassName: "border-border"
  },
  verified: {
    label: "Provably attested",
    icon: ShieldCheck,
    className: "text-foreground",
    borderClassName: "border-border-strong"
  },
  unverified: {
    label: "Attestation unverified",
    icon: ShieldAlert,
    className: "text-foreground",
    borderClassName: "border-border"
  },
  stale: {
    label: "Attestation check stale",
    icon: ShieldAlert,
    className: "text-foreground",
    borderClassName: "border-border"
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
  const { data: result, refetch, isFetching } = useAttestation();
  const checkedAt = result ? new Date(result.checkedAt) : null;
  const status: DisplayStatus = !result ? "checking" : result.status === "verified" ? "verified" : "unverified";

  const effective: DisplayStatus =
    status === "verified" &&
    checkedAt &&
    Date.now() - checkedAt.getTime() > STALE_AFTER_MS
      ? "stale"
      : status;

  const { label, icon: Icon, className, borderClassName } = DISPLAY[effective];

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
            borderClassName
          )}
        >
          <Icon className={cn("h-3.5 w-3.5 shrink-0", className)} strokeWidth={1.5} />
          <span
            className={cn(
              "min-w-0 truncate text-[11px] font-medium uppercase tracking-label",
              className
            )}
          >
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Icon className={cn("h-4 w-4", className)} strokeWidth={1.5} />
            {label}
          </DialogTitle>
          <DialogDescription>{summary}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-sm text-muted-foreground">
          <p>
            This runs independently on this app&apos;s own server: it does not simply
            trust a flag the inference API hands back, and it does not trust the
            inference API&apos;s claimed signing key either. It checks that claimed key
            against <code className="font-mono text-xs text-foreground">PLATFORM_PUBKEY</code>, this
            deployment&apos;s own pinned copy of the same key subnet validators and TEEs
            pin, fetches the signed model allowlist from{" "}
            <code className="font-mono text-xs text-foreground">GET /allowlist</code>, then recomputes and
            checks the Ed25519 signature itself.
          </p>
          {provenAt ? (
            <p>
              <span className="font-medium text-foreground">Proven at: </span>
              {formatDateTime(provenAt.toISOString())}. This is when this allowlist was last
              (re)signed with its current content, not when this page happened to check it.
            </p>
          ) : null}
          {checkedAt ? <p>Last checked: {formatDateTime(checkedAt.toISOString())}</p> : null}
          {result?.platformPubkey ? (
            <p className="break-all">
              Pinned platform key:{" "}
              <code className="font-mono text-xs text-foreground">{result.platformPubkey}</code>
            </p>
          ) : null}
          {result?.entries && result.entries.length > 0 ? (
            <div>
              <p className="font-medium text-foreground">Attested models:</p>
              <ul className="mt-1 space-y-1">
                {result.entries.map((entry, i) => (
                  <li key={`${entry.model_id}-${entry.model_version ?? ""}-${i}`} className="break-all">
                    <code className="font-mono text-xs text-foreground">
                      {entry.model_id}
                      {entry.model_version ? `@${entry.model_version}` : ""}
                    </code>: hash <code className="font-mono text-xs text-foreground">{entry.model_hash}</code>
                    {entry.gpu_hash ? (
                      <>
                        {" "}
                        · gpu <code className="font-mono text-xs text-foreground">{entry.gpu_hash}</code>
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p>Automatically re-checked every 6 hours while this page is open.</p>
          <Button variant="outline" disabled={isFetching} onClick={() => void refetch({ cancelRefetch: false })}>
            {isFetching ? "Checking…" : "Check now"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
