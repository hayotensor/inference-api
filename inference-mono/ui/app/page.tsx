"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  CreditCard,
  KeyRound,
  ShieldCheck,
  WalletCards
} from "lucide-react";

import { PublicNav } from "@/components/public-nav";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { apiKeysApi, billingApi, usageApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatNumber } from "@/lib/format";

const capabilities = [
  {
    icon: KeyRound,
    title: "API keys",
    description: "Create scoped test and live keys, revoke them, and inspect usage metadata."
  },
  {
    icon: BarChart3,
    title: "Usage",
    description: "Review current allowance, period dates, token usage, and request history."
  },
  {
    icon: CreditCard,
    title: "Billing",
    description: "Open backend-created Stripe Checkout and customer portal sessions."
  },
  {
    icon: WalletCards,
    title: "Wallets",
    description: "Connect an EVM wallet, sign a backend nonce, and display verified balances."
  }
];

// Shown to signed-out visitors only — a representative example, never presented
// as this viewer's own data (see the "Example account" label it renders under).
const EXAMPLE_STATS: [string, string, string][] = [
  ["Remaining tokens", "842,400", "Current period"],
  ["Active keys", "4", "2 live, 2 test"],
  ["Wallet allowance", "120,000", "EVM verified"],
  ["Subscription", "Pro", "Renews monthly"]
];

export default function HomePage() {
  const { isAuthenticated } = useAuth();

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
          "Remaining tokens",
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
    <div className="min-h-screen bg-background">
      <PublicNav />
      <main>
        <section className="border-b border-border bg-background">
          <div className="page-shell grid min-h-[calc(100vh-4rem)] items-center gap-10 py-12 lg:grid-cols-[0.92fr_1.08fr]">
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
                Verifiable inference.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-muted-foreground">
                Manage access, subscriptions, token usage, wallet verification, and demo
                inference from one console. Auth, billing, and metering stay owned by the
                API — the dashboard never duplicates that logic.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <Button size="lg" asChild>
                  <Link href="/signup">
                    Create account
                    <ArrowRight strokeWidth={1.5} />
                  </Link>
                </Button>
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

        <section className="page-shell py-24">
          <p className="eyebrow mb-6">Capabilities</p>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {capabilities.map((item, i) => {
              const Icon = item.icon;
              return (
                <Card key={item.title} interactive className="flex flex-col">
                  <div className="flex items-center justify-between border-b border-border px-5 py-3">
                    <Icon className="h-4 w-4 text-muted-foreground" strokeWidth={1.5} />
                    <span className="metadata text-xs">
                      {String(i + 1).padStart(2, "0")} / {String(capabilities.length).padStart(2, "0")}
                    </span>
                  </div>
                  <CardContent className="flex-1 p-5 pt-5">
                    <h2 className="font-display text-base font-medium tracking-display">
                      {item.title}
                    </h2>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      {item.description}
                    </p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </section>
      </main>
    </div>
  );
}
