"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, ShieldCheck } from "lucide-react";

import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiKeysApi, billingApi, usageApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { buildChatPromptUrl } from "@/lib/chat-prompt";
import { formatNumber } from "@/lib/format";

// Shown to signed-out visitors only — a representative example, never presented
// as this viewer's own data (see the "Example account" label it renders under).
const EXAMPLE_STATS: [string, string, string][] = [
  ["Credits remaining", "842,400", "Current period"],
  ["Active keys", "4", "2 live, 2 test"],
  ["Wallet allowance", "120,000", "On-chain balance"],
  ["Subscription", "Pro", "Renews monthly"]
];

export default function HomePage() {
  const { isAuthenticated, ensureSsoCookie } = useAuth();
  const [prompt, setPrompt] = useState("");

  async function handlePromptSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed) return;
    try {
      // Best-effort: mints the SSO cookie when a session exists so the new
      // tab lands signed in; a no-op when signed out, in which case the
      // chat app's own front door sends the visitor to /login and back.
      await ensureSsoCookie();
    } catch {
      // Still open the tab — the front door will redirect if needed.
    }
    window.open(buildChatPromptUrl(trimmed), "_blank", "noopener,noreferrer");
  }

  function handlePromptKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  const usageQuery = useQuery({
    queryKey: ["usage-current"],
    queryFn: usageApi.current,
    enabled: isAuthenticated
  });
  const subscriptionQuery = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: billingApi.subscription,
    enabled: isAuthenticated
  });
  const keysQuery = useQuery({
    queryKey: ["api-keys"],
    queryFn: apiKeysApi.list,
    enabled: isAuthenticated
  });

  const activeKeyCount = keysQuery.data?.filter((key) => {
    const expired = key.expires_at ? new Date(key.expires_at).getTime() < Date.now() : false;
    return !key.revoked_at && !expired;
  }).length;

  const stats: [string, string, string][] = isAuthenticated
    ? [
        [
          "Credits remaining",
          usageQuery.data ? formatNumber(usageQuery.data.remaining_tokens) : "—",
          usageQuery.data ? `${formatNumber(usageQuery.data.used_tokens)} used` : "Current period"
        ],
        [
          "Active keys",
          keysQuery.data ? formatNumber(activeKeyCount ?? 0) : "—",
          keysQuery.data ? `${formatNumber(keysQuery.data.length)} total` : ""
        ],
        [
          "Wallet allowance",
          usageQuery.data ? formatNumber(usageQuery.data.ethereum_erc20_allowance) : "—",
          "EVM + substrate"
        ],
        [
          "Subscription",
          subscriptionQuery.data?.plan_name ?? (subscriptionQuery.isLoading ? "—" : "Free"),
          subscriptionQuery.data?.status ?? ""
        ]
      ]
    : EXAMPLE_STATS;

  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main>
        <section className="bg-background">
          <div className="page-shell grid min-h-[calc(100dvh-4rem)] items-center gap-10 py-12 lg:grid-cols-[0.92fr_1.08fr]">
            <div className="max-w-2xl">
              <div className="mb-6 inline-flex items-center gap-2 rounded-sm border border-border px-3 py-1.5 text-[11px] font-medium uppercase tracking-label text-muted-foreground">
                <ShieldCheck className="h-3.5 w-3.5 text-foreground" strokeWidth={1.5} />
                API-owned auth, billing, usage, and wallet verification
              </div>
              {/* Display headline: Sora Light at the kit's display size, short
                  period-terminated sentences, one headline per surface. */}
              <h1 className="font-display text-[44px] font-light leading-[52px] tracking-display sm:text-[56px] sm:leading-[64px]">
                Open infrastructure.
                <br />
                Inference with receipts.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-muted-foreground">
                Manage access, subscriptions, credit usage, wallet verification, and demo
                inference from one console. Auth, billing, and metering stay owned by the
                API — the dashboard never duplicates that logic.
              </p>

              <form onSubmit={handlePromptSubmit} className="mt-8 max-w-xl">
                <Label htmlFor="home-prompt">Ask something</Label>
                <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-end">
                  <Textarea
                    id="home-prompt"
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    onKeyDown={handlePromptKeyDown}
                    placeholder="Ask anything. Opens in a new chat tab — press Enter there to send."
                    rows={2}
                    className="min-h-[52px] flex-1 resize-none"
                  />
                  <Button type="submit" size="lg" disabled={!prompt.trim()} className="sm:shrink-0">
                    Ask
                    <ArrowRight strokeWidth={1.5} />
                  </Button>
                </div>
                <p className="metadata mt-2 text-xs">
                  Opens the chat app in a new tab with this prompt ready to send — nothing is
                  sent until you press Enter there.
                </p>
              </form>

              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                {isAuthenticated ? (
                  <Button size="lg" asChild>
                    <Link href="/dashboard">
                      Go to dashboard
                      <ArrowRight strokeWidth={1.5} />
                    </Link>
                  </Button>
                ) : (
                  <Button size="lg" asChild>
                    <Link href="/signup">
                      Create account
                      <ArrowRight strokeWidth={1.5} />
                    </Link>
                  </Button>
                )}
                <Button size="lg" variant="outline" asChild>
                  <Link href="/pricing">View pricing</Link>
                </Button>
              </div>
            </div>

            <div className="rounded-2xl border border-border bg-card">
              <div className="border-b border-border p-5">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="eyebrow">Developer console</p>
                    <p className="metadata mt-1 text-xs">
                      {isAuthenticated ? "your account · live" : "example account"}
                    </p>
                  </div>
                  {/* Filled Eggshell is the affirmative terminal state; the
                      placeholder stays a quiet outlined tag. See ui/badge.tsx. */}
                  <Badge variant={isAuthenticated ? "success" : "secondary"}>
                    {isAuthenticated ? "Synced" : "Sample data"}
                  </Badge>
                </div>
              </div>
              {/* gap-px over the border color draws the grid as hairlines rather
                  than as a stack of nested boxes. */}
              <div className="grid gap-px bg-border sm:grid-cols-2">
                {stats.map(([label, value, detail]) => (
                  <div key={label} className="bg-card p-5">
                    <p className="eyebrow">{label}</p>
                    <p className="numeral mt-3 text-2xl font-medium">{value}</p>
                    <p className="metadata mt-1 text-xs">{detail}</p>
                  </div>
                ))}
              </div>
              <div className="border-t border-border p-5">
                <div>
                  {[
                    "POST /auth/login",
                    "POST /billing/stripe/checkout-session",
                    "POST /wallets/evm/verify",
                    "GET /usage/allowance"
                  ].map((endpoint) => (
                    <div
                      key={endpoint}
                      className="flex items-center justify-between border-t border-border px-1 py-2.5 font-mono text-xs text-muted-foreground first:border-t-0"
                    >
                      <span>{endpoint}</span>
                      <CheckCircle2 className="h-3.5 w-3.5 text-foreground" strokeWidth={1.5} />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
