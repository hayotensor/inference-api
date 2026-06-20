"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  CreditCard,
  KeyRound,
  RefreshCw,
  WalletCards
} from "lucide-react";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { apiKeysApi, billingApi, usageApi, walletsApi } from "@/lib/api";
import { formatDate, formatNumber, formatPercent, shortenAddress } from "@/lib/format";

export default function DashboardPage() {
  const usageQuery = useQuery({
    queryKey: ["usage-current"],
    queryFn: usageApi.current
  });
  const subscriptionQuery = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: billingApi.subscription
  });
  const keysQuery = useQuery({
    queryKey: ["api-keys"],
    queryFn: apiKeysApi.list
  });
  const walletQuery = useQuery({
    queryKey: ["wallet-evm"],
    queryFn: walletsApi.get
  });

  const usage = usageQuery.data;
  const percent = usage
    ? formatPercent(usage.used_tokens, usage.total_allowance)
    : 0;
  const activeKeys =
    keysQuery.data?.filter((key) => {
      const expired = key.expires_at
        ? new Date(key.expires_at).getTime() < Date.now()
        : false;
      return !key.revoked_at && !expired;
    }).length ?? 0;

  return (
    <>
      <DashboardPageHeader
        title="Overview"
        description="Account state loaded directly from the FastAPI API."
        action={
          <Button variant="outline" onClick={() => void usageQuery.refetch()}>
            <RefreshCw />
            Refresh usage
          </Button>
        }
      />

      {usageQuery.isLoading ? <LoadingState /> : null}
      {usageQuery.isError ? (
        <ErrorState error={usageQuery.error} onRetry={() => void usageQuery.refetch()} />
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Remaining tokens"
          value={formatNumber(usage?.remaining_tokens)}
          detail={usage ? `Through ${formatDate(usage.period_end)}` : undefined}
          icon={BarChart3}
          tone="green"
        />
        <StatCard
          label="Used tokens"
          value={formatNumber(usage?.used_tokens)}
          detail={`${percent}% of allowance`}
          icon={BarChart3}
          tone="blue"
        />
        <StatCard
          label="Active keys"
          value={formatNumber(activeKeys)}
          detail={`${keysQuery.data?.length ?? 0} total keys`}
          icon={KeyRound}
          tone="amber"
        />
        <StatCard
          label="Wallet"
          value={walletQuery.data ? "Verified" : "Not linked"}
          detail={shortenAddress(walletQuery.data?.address)}
          icon={WalletCards}
          tone="neutral"
        />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Token allowance</CardTitle>
            <CardDescription>
              Displaying the current usage period exactly as returned by the API.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {usage ? (
              <div className="space-y-5">
                <Progress value={percent} />
                <div className="grid gap-3 sm:grid-cols-2">
                  {[
                    ["Stripe allowance", usage.stripe_allowance],
                    ["Ethereum ERC20 allowance", usage.ethereum_erc20_allowance],
                    ["Substrate native allowance", usage.substrate_native_allowance],
                    ["Manual allowance", usage.manual_allowance]
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg border bg-muted/30 p-3">
                      <p className="text-xs text-muted-foreground">{label}</p>
                      <p className="mt-1 font-semibold">{formatNumber(value)}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Usage data is unavailable.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Subscription</CardTitle>
            <CardDescription>Billing status is not calculated in the UI.</CardDescription>
          </CardHeader>
          <CardContent>
            {subscriptionQuery.isLoading ? <LoadingState /> : null}
            {subscriptionQuery.data ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <Badge variant={subscriptionQuery.data.status === "active" ? "success" : "outline"}>
                    {subscriptionQuery.data.status}
                  </Badge>
                </div>
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="text-muted-foreground">Plan</span>
                  <span className="font-medium">
                    {subscriptionQuery.data.plan_name ?? "No paid plan"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3 text-sm">
                  <span className="text-muted-foreground">Period end</span>
                  <span className="font-medium">
                    {formatDate(subscriptionQuery.data.current_period_end)}
                  </span>
                </div>
                <Button className="w-full" asChild>
                  <Link href="/dashboard/billing">
                    <CreditCard />
                    Manage billing
                  </Link>
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
