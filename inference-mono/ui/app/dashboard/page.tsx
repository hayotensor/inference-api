"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  CreditCard,
  KeyRound,
  Loader2,
  RefreshCw,
  WalletCards
} from "lucide-react";
import { toast } from "sonner";
import { useWallet } from "@/lib/wallet";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { StatusBadge, SubscriptionStatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { WalletConnectButton } from "@/components/wallet-connect-button";
import { apiErrorMessage, apiKeysApi, billingApi, usageApi, walletsApi } from "@/lib/api";
import {
  formatDate,
  formatDateTime,
  formatNumber,
  formatPercent,
  shortenAddress
} from "@/lib/format";
import type { EvmWallet } from "@/lib/types";

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { address: connectedAddress, isConnected, signMessageAsync } = useWallet();

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

  const verifyMutation = useMutation({
    mutationFn: async () => {
      if (!connectedAddress) throw new Error("Connect a wallet first.");
      const nonce = await walletsApi.nonce(connectedAddress);
      const signature = await signMessageAsync({ message: nonce.message });
      return walletsApi.verify({
        address: nonce.address,
        message: nonce.message,
        signature
      });
    },
    onSuccess: async () => {
      toast.success("Wallet verified");
      await queryClient.invalidateQueries({ queryKey: ["wallet-evm"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-current"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-allowance"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
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
        description="Your account overview."
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
          label="Credits remaining"
          value={formatNumber(usage?.remaining_tokens)}
          detail={usage ? `Through ${formatDate(usage.period_end)}` : undefined}
          icon={BarChart3}
        />
        <StatCard
          label="Credits used"
          value={formatNumber(usage?.used_tokens)}
          detail={`${percent}% of allowance`}
          icon={BarChart3}
        />
        <StatCard
          label="Active keys"
          value={formatNumber(activeKeys)}
          detail={`${keysQuery.data?.length ?? 0} total keys`}
          icon={KeyRound}
        />
        <WalletStatCard
          wallet={walletQuery.data}
          connectedAddress={connectedAddress}
          isConnected={isConnected}
          onVerify={() => verifyMutation.mutate()}
          verifying={verifyMutation.isPending}
        />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Credit allowance</CardTitle>
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
                <p className="text-xs text-muted-foreground">
                  Credits are what every request draws from. Each model has its own rate,
                  so the raw tokens a request uses aren&apos;t always a 1:1 credit cost. See{" "}
                  <Link href="/dashboard/usage" className="underline">
                    Usage
                  </Link>{" "}
                  for the per-model breakdown.
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Usage data is unavailable.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Subscription</CardTitle>
            <CardDescription>Billing status.</CardDescription>
          </CardHeader>
          <CardContent>
            {subscriptionQuery.isLoading ? <LoadingState /> : null}
            {subscriptionQuery.data ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <SubscriptionStatusBadge
                    status={subscriptionQuery.data.status}
                    cancelAtPeriodEnd={subscriptionQuery.data.cancel_at_period_end}
                  />
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

/**
 * The one wallet element on Overview -- same tile chrome as `StatCard`
 * (eyebrow + icon over a border, content below) so it sits in that grid
 * without looking bolted on, but the content is live and actionable instead
 * of a static value/detail pair: connect, verify & link, or manage, right
 * from this tile. See `/dashboard/wallet` for sync/unlink.
 */
function WalletStatCard({
  wallet,
  connectedAddress,
  isConnected,
  onVerify,
  verifying
}: {
  wallet: EvmWallet | null | undefined;
  connectedAddress: `0x${string}` | undefined;
  isConnected: boolean;
  onVerify: () => void;
  verifying: boolean;
}) {
  return (
    <Card className="flex flex-col">
      <div className="flex items-center justify-between gap-3 border-b px-5 py-3">
        <span className="eyebrow truncate">Wallet</span>
        <WalletCards className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.5} />
      </div>
      <div className="flex flex-1 flex-col justify-between gap-3 px-5 pb-5 pt-4">
        <div>
          <StatusBadge status={wallet ? "verified" : isConnected ? "connected" : "not_connected"} />
          <p className="numeral mt-2 truncate text-lg font-medium leading-6">
            {wallet
              ? shortenAddress(wallet.address)
              : (connectedAddress && shortenAddress(connectedAddress)) || "No wallet"}
          </p>
          <p className="metadata mt-1 truncate text-xs text-muted-foreground">
            {wallet ? `Verified ${formatDateTime(wallet.verified_at)}` : "Not linked"}
          </p>
        </div>

        {wallet ? (
          <Button variant="outline" size="sm" className="w-full" asChild>
            <Link href="/dashboard/wallet">
              <WalletCards />
              Manage
            </Link>
          </Button>
        ) : isConnected ? (
          <Button size="sm" className="w-full" onClick={onVerify} disabled={verifying}>
            {verifying ? <Loader2 className="animate-spin" /> : <WalletCards />}
            Verify & link
          </Button>
        ) : (
          <WalletConnectButton size="sm" className="w-full" />
        )}
      </div>
    </Card>
  );
}
