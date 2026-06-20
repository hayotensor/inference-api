import Link from "next/link";
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
import { Card, CardContent } from "@/components/ui/card";

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

export default function HomePage() {
  return (
    <div className="min-h-screen bg-background">
      <PublicNav />
      <main>
        <section className="border-b bg-muted/30">
          <div className="page-shell grid min-h-[calc(100vh-4rem)] items-center gap-10 py-12 lg:grid-cols-[0.92fr_1.08fr]">
            <div className="max-w-2xl">
              <div className="mb-5 inline-flex items-center gap-2 rounded-md border bg-background px-3 py-1 text-sm text-muted-foreground">
                <ShieldCheck className="h-4 w-4 text-emerald-600" />
                API-owned auth, billing, usage, and wallet verification
              </div>
              <h1 className="text-4xl font-semibold tracking-normal sm:text-5xl">
                Inference API
              </h1>
              <p className="mt-5 text-lg leading-8 text-muted-foreground">
                A production dashboard for developers to manage access, subscriptions,
                token usage, wallet verification, and demo inference without duplicating
                backend business logic.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                <Button size="lg" asChild>
                  <Link href="/signup">
                    Create account
                    <ArrowRight />
                  </Link>
                </Button>
                <Button size="lg" variant="outline" asChild>
                  <Link href="/pricing">View pricing</Link>
                </Button>
              </div>
            </div>

            <div className="rounded-lg border bg-background shadow-sm">
              <div className="border-b p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">Developer console</p>
                    <p className="text-xs text-muted-foreground">Live account snapshot</p>
                  </div>
                  <div className="rounded-md bg-emerald-100 px-2 py-1 text-xs font-medium text-emerald-800">
                    Synced
                  </div>
                </div>
              </div>
              <div className="grid gap-4 p-4 sm:grid-cols-2">
                {[
                  ["Remaining tokens", "842,400", "Current period"],
                  ["Active keys", "4", "2 live, 2 test"],
                  ["Wallet allowance", "120,000", "EVM verified"],
                  ["Subscription", "Pro", "Renews monthly"]
                ].map(([label, value, detail]) => (
                  <Card key={label} className="shadow-none">
                    <CardContent className="p-4">
                      <p className="text-xs text-muted-foreground">{label}</p>
                      <p className="mt-2 text-2xl font-semibold tracking-normal">{value}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
                    </CardContent>
                  </Card>
                ))}
              </div>
              <div className="border-t p-4">
                <div className="space-y-3">
                  {[
                    "POST /auth/login",
                    "POST /billing/stripe/checkout-session",
                    "POST /wallets/evm/verify",
                    "GET /usage/allowance"
                  ].map((endpoint) => (
                    <div
                      key={endpoint}
                      className="flex items-center justify-between rounded-md bg-muted px-3 py-2 font-mono text-xs"
                    >
                      <span>{endpoint}</span>
                      <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="page-shell py-14">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {capabilities.map((item) => {
              const Icon = item.icon;
              return (
                <Card key={item.title}>
                  <CardContent className="p-5">
                    <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
                      <Icon className="h-5 w-5" />
                    </div>
                    <h2 className="font-semibold">{item.title}</h2>
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
