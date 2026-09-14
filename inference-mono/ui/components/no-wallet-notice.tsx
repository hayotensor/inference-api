"use client";

import * as React from "react";
import { TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * HYP-51: RainbowKit's connect modal already knows whether an extension
 * wallet is installed -- unavailable ones render as "Get" links inside the
 * modal -- but that only shows up *after* a visitor clicks connect. A
 * browser with no injected wallet at all (no MetaMask, Rabby, Coinbase
 * Wallet, ...) otherwise looks identical to one that does, so someone
 * without a wallet has no indication before clicking that connecting
 * requires installing something (or pairing a mobile/hardware wallet via
 * WalletConnect instead). This renders a small, page-visible notice next to
 * the connect affordance up front instead of leaving that only discoverable
 * inside the modal.
 *
 * Detection covers both the legacy single-provider convention
 * (`window.ethereum`, still how most wallets announce today) and EIP-6963's
 * multi-injected-provider discovery event, the same signal RainbowKit's own
 * wallet list is built from.
 */
export function NoWalletNotice({ className }: { className?: string }) {
  // `null` = not yet determined (server render / before the detection effect
  // runs) -- rendering nothing until we know avoids a hydration mismatch and
  // avoids a false "no wallet" flash for people who do have one.
  const [hasInjectedWallet, setHasInjectedWallet] = React.useState<boolean | null>(null);

  React.useEffect(() => {
    if (typeof window === "undefined") return;

    const legacyInjected = Boolean((window as unknown as { ethereum?: unknown }).ethereum);
    if (legacyInjected) {
      setHasInjectedWallet(true);
      return;
    }

    // EIP-6963: ask every installed wallet extension to announce itself.
    // Announcements arrive as a burst of synchronous-ish events right after
    // the request event fires, so a short window covers them without
    // leaving the "unknown" state hanging indefinitely.
    let found = false;
    const onAnnounce = () => {
      found = true;
      setHasInjectedWallet(true);
    };
    window.addEventListener("eip6963:announceProvider", onAnnounce);
    window.dispatchEvent(new Event("eip6963:requestProvider"));

    const timer = window.setTimeout(() => {
      if (!found) setHasInjectedWallet(false);
    }, 250);

    return () => {
      window.removeEventListener("eip6963:announceProvider", onAnnounce);
      window.clearTimeout(timer);
    };
  }, []);

  if (hasInjectedWallet !== false) return null;

  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-2 rounded-md border border-border-strong bg-card px-3 py-2",
        className
      )}
    >
      <TriangleAlert
        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
        strokeWidth={1.5}
      />
      <p className="text-xs leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">No browser wallet detected.</span>{" "}
        Install{" "}
        <a
          href="https://metamask.io/download/"
          target="_blank"
          rel="noreferrer"
          className="underline underline-offset-2 hover:text-foreground"
        >
          MetaMask
        </a>{" "}
        (or another extension wallet), or use WalletConnect below to pair a mobile or
        hardware wallet instead.
      </p>
    </div>
  );
}
