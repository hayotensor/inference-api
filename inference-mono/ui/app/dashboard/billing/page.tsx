"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, CreditCard, ExternalLink, Loader2, RefreshCw, Wallet, XCircle } from "lucide-react";
import { toast } from "sonner";

import { BillingIntervalToggle } from "@/components/billing-interval-toggle";
import { DashboardPageHeader } from "@/components/dashboard-shell";
import { ErrorState, LoadingState } from "@/components/data-state";
import { StatusBadge, SubscriptionStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import {
  apiErrorMessage,
  billingApi,
  redirectToStripeSession
} from "@/lib/api";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import {
  annualSavingsPercent,
  isStripeTestMode,
  planIsFree,
  planIsPurchasable,
  planPriceCents
} from "@/lib/stripe";
import type { BillingInterval, WalletCheckoutRequest } from "@/lib/types";

export default function BillingPage() {
  const queryClient = useQueryClient();
  const [interval, setInterval] = React.useState<BillingInterval>("month");
  const [selectedBundleId, setSelectedBundleId] = React.useState<string | null>(null);
  const [showCustomAmount, setShowCustomAmount] = React.useState(false);
  const [customAmount, setCustomAmount] = React.useState("");
  const subscriptionQuery = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: billingApi.subscription
  });
  const plansQuery = useQuery({
    queryKey: ["billing-plans"],
    queryFn: billingApi.plans
  });
  const walletBalanceQuery = useQuery({
    queryKey: ["wallet-balance"],
    queryFn: billingApi.walletBalance
  });
  const walletBundlesQuery = useQuery({
    queryKey: ["wallet-bundles"],
    queryFn: billingApi.walletBundles
  });

  const portalMutation = useMutation({
    mutationFn: async (portalWindow: Window) => {
      const response = await billingApi.portal();
      const url = response.url ?? response.checkout_url ?? response.session_url ?? response.portal_url;
      if (!url) {
        throw new Error("The API did not return a Stripe redirect URL.");
      }
      portalWindow.location.replace(url);
    },
    onError: (error, portalWindow) => {
      portalWindow.close();
      toast.error(apiErrorMessage(error));
    }
  });
  const checkoutMutation = useMutation({
    mutationFn: ({ planId, interval: checkoutInterval }: { planId: string; interval: BillingInterval }) =>
      billingApi.checkout(planId, checkoutInterval),
    onSuccess: redirectToStripeSession,
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const changeMutation = useMutation({
    mutationFn: ({ planId, interval: nextInterval }: { planId: string; interval: BillingInterval }) =>
      billingApi.changePlan(planId, nextInterval),
    onSuccess: async () => {
      toast.success("Plan updated: The difference will be prorated");
      await queryClient.invalidateQueries({ queryKey: ["billing-subscription"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-allowance"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const cancelMutation = useMutation({
    mutationFn: () => billingApi.cancel(true),
    onSuccess: async (subscription) => {
      const activeUntil = subscription.current_period_end
        ? formatDate(subscription.current_period_end)
        : "the end of your current billing period";
      toast.success(`Renewal canceled. Your plan remains active until ${activeUntil}.`);
      await queryClient.invalidateQueries({ queryKey: ["billing-subscription"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const walletCheckoutMutation = useMutation({
    mutationFn: (request: WalletCheckoutRequest) => billingApi.walletCheckout(request),
    onSuccess: redirectToStripeSession,
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const plans = (plansQuery.data ?? []).filter((plan) => plan.active);
  const savingsPercent = plans.reduce<number | null>(
    (best, plan) => Math.max(best ?? 0, annualSavingsPercent(plan) ?? 0) || null,
    null
  );
  const annualAvailable = plans.some((plan) => planIsPurchasable(plan, "year"));
  const subscription = subscriptionQuery.data;
  const hasActiveSubscription = Boolean(
    subscription && ["active", "trialing", "past_due"].includes(subscription.status)
  );
  const selectedBundle = walletBundlesQuery.data?.find((bundle) => bundle.id === selectedBundleId);
  const customAmountCents = /^\d+(\.\d{1,2})?$/.test(customAmount)
    ? Math.round(Number(customAmount) * 100)
    : 0;
  const customAmountValid = customAmountCents >= 1_000 && customAmountCents <= 99_999_999;
  const customAmountTier = (walletBundlesQuery.data ?? [])
    .filter((bundle) => bundle.usd_cents <= customAmountCents)
    .sort((a, b) => b.usd_cents - a.usd_cents)[0];
  const customAmountCredits = customAmountValid && customAmountTier
    ? Math.floor(customAmountCents * customAmountTier.tokens / customAmountTier.usd_cents)
    : null;
  const walletCheckoutRequest: WalletCheckoutRequest | null = showCustomAmount
    ? customAmountCredits !== null
      ? { usd_cents: customAmountCents }
      : null
    : selectedBundle
      ? { bundle_id: selectedBundle.id }
      : null;

  return (
    <>
      <DashboardPageHeader
        title="Billing"
        description="Manage your credits."
        action={
          <div className="flex items-center gap-2">
            {isStripeTestMode ? <Badge variant="warning">Stripe test mode</Badge> : null}
            <Button variant="outline" onClick={() => void subscriptionQuery.refetch()}>
              <RefreshCw />
              Refresh
            </Button>
          </div>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Current subscription</CardTitle>
            <CardDescription>Your plan, billing period, and renewal details.</CardDescription>
          </CardHeader>
          <CardContent>
            {subscriptionQuery.isLoading ? <LoadingState /> : null}
            {subscriptionQuery.isError ? <ErrorState error={subscriptionQuery.error} /> : null}
            {subscription ? (
              <div className="space-y-4 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Status</span>
                  <SubscriptionStatusBadge
                    status={subscription.status}
                    cancelAtPeriodEnd={subscription.cancel_at_period_end}
                  />
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-muted-foreground">Price</span>
                  <span className="text-sm font-semibold tracking-normal">
                    {subscription.amount_cents !== null && subscription.amount_cents !== undefined
                      ? formatMoney(subscription.amount_cents, subscription.currency)
                      : "$0"}
                    <span className="text-sm font-normal text-muted-foreground">
                      {subscription.interval === "year" ? " / year" : " / month"}
                    </span>
                  </span>
                </div>
                {[
                  ["Plan", subscription.plan_name ?? "Free"],
                  ["Period start", formatDate(subscription.current_period_start)],
                  ["Period end", formatDate(subscription.current_period_end)],
                  ["Cancel at period end", subscription.cancel_at_period_end ? "Yes" : "No"]
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="max-w-[60%] truncate font-medium">{value}</span>
                  </div>
                ))}
                <div className="grid gap-2 pt-2 sm:grid-cols-2">
                  <Button
                    onClick={() => {
                      // Open during the click so the async request does not trigger popup blocking.
                      const portalWindow = window.open("about:blank", "_blank");
                      if (!portalWindow) {
                        toast.error("Allow popups to open the customer portal in a new tab.");
                        return;
                      }
                      portalWindow.opener = null;
                      portalMutation.mutate(portalWindow);
                    }}
                    disabled={portalMutation.isPending}
                  >
                    {portalMutation.isPending ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <ExternalLink />
                    )}
                    Customer portal
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => cancelMutation.mutate()}
                    disabled={
                      cancelMutation.isPending ||
                      subscription.status !== "active" ||
                      subscription.cancel_at_period_end
                    }
                  >
                    {cancelMutation.isPending ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <XCircle />
                    )}
                    Cancel renewal
                  </Button>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle>Plans</CardTitle>
                <CardDescription>
                  {hasActiveSubscription
                    ? "Switching plans updates the subscription in place; the difference prorates."
                    : "Choosing a paid plan opens Checkout."}{" "}
                </CardDescription>
              </div>
              {annualAvailable ? (
                <BillingIntervalToggle
                  value={interval}
                  onChange={setInterval}
                  savingsPercent={savingsPercent}
                />
              ) : null}
            </div>
          </CardHeader>
          <CardContent>
            {plansQuery.isLoading ? <LoadingState /> : null}
            {plansQuery.isError ? <ErrorState error={plansQuery.error} /> : null}
            {plansQuery.data ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Plan</TableHead>
                    <TableHead>Credits / mo</TableHead>
                    <TableHead>Price</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plans.map((plan) => {
                    // A canceled subscription keeps its plan_id (so "what plan you
                    // were on" stays visible), so matching on plan_id alone would
                    // still mark that plan "Current" with no way to re-choose it.
                    const current = subscription?.plan_id === plan.id && hasActiveSubscription;
                    const isFree = planIsFree(plan);
                    const purchasable = planIsPurchasable(plan, interval);
                    const priceCents = isFree ? 0 : planPriceCents(plan, interval);
                    // Both mutations are shared across every row in the table, so
                    // isPending alone would spin every button at once — only the
                    // row whose plan/interval matches the in-flight call should.
                    const isThisRowChanging =
                      changeMutation.isPending &&
                      changeMutation.variables?.planId === plan.id &&
                      changeMutation.variables?.interval === interval;
                    const isThisRowCheckingOut =
                      checkoutMutation.isPending &&
                      checkoutMutation.variables?.planId === plan.id &&
                      checkoutMutation.variables?.interval === interval;
                    return (
                      <TableRow key={plan.id}>
                        <TableCell className="font-medium">{plan.name}</TableCell>
                        <TableCell>
                          {plan.is_unlimited ? "Unlimited" : formatNumber(plan.monthly_token_allowance)}
                        </TableCell>
                        <TableCell>
                          {isFree || purchasable ? (
                            <span>
                              {formatMoney(priceCents, plan.currency)}
                              <span className="text-muted-foreground">
                                {interval === "year" && !isFree ? " / yr" : " / mo"}
                              </span>
                            </span>
                          ) : (
                            <span className="text-muted-foreground">Not offered</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          {current ? (
                            <StatusBadge status="current" />
                          ) : isFree ? null : !purchasable ? (
                            <span className="text-sm text-muted-foreground">—</span>
                          ) : hasActiveSubscription ? (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => changeMutation.mutate({ planId: plan.id, interval })}
                              disabled={isThisRowChanging}
                            >
                              {isThisRowChanging ? (
                                <Loader2 className="animate-spin" />
                              ) : (
                                <ArrowLeftRight />
                              )}
                              Switch
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              onClick={() => checkoutMutation.mutate({ planId: plan.id, interval })}
                              disabled={isThisRowCheckingOut}
                            >
                              {isThisRowCheckingOut ? (
                                <Loader2 className="animate-spin" />
                              ) : (
                                <CreditCard />
                              )}
                              Choose
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pay-as-you-go</CardTitle>
            <CardDescription>
              Once the plan&apos;s allowance for the period runs
              out, usage draws from this balance instead of being blocked.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {walletBalanceQuery.isLoading ? <LoadingState /> : null}
            {walletBalanceQuery.isError ? <ErrorState error={walletBalanceQuery.error} /> : null}
            {walletBalanceQuery.data ? (
              <div className="space-y-4 text-sm">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-muted-foreground font-medium">Pay-as-you-go credits</span>
                  <span className="text-2xl font-semibold tracking-normal">
                    {formatNumber(walletBalanceQuery.data.balance_tokens)}
                  </span>
                </div>
                <form
                  className="space-y-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!walletCheckoutRequest || walletCheckoutMutation.isPending) return;
                    walletCheckoutMutation.mutate(walletCheckoutRequest);
                  }}
                >
                  <div className="my-4 border-t-2 border-gray-100"></div>
                  <span className="text-muted-foreground font-medium">Top up</span>
                  {walletBundlesQuery.isLoading ? <LoadingState /> : null}
                  {walletBundlesQuery.isError ? <ErrorState error={walletBundlesQuery.error} /> : null}
                  <div className="grid gap-2 sm:grid-cols-2">
                    {(walletBundlesQuery.data ?? []).map((bundle) => {
                      const isSelected = selectedBundleId === bundle.id;
                      return (
                        <Button
                          key={bundle.id}
                          type="button"
                          variant={isSelected ? "default" : "outline"}
                          aria-pressed={isSelected}
                          onClick={() => {
                            setSelectedBundleId(bundle.id);
                            setShowCustomAmount(false);
                          }}
                          disabled={walletCheckoutMutation.isPending}
                        >
                          <Wallet />
                          {formatMoney(bundle.usd_cents, bundle.currency)} for{" "}
                          {formatNumber(bundle.tokens)}
                        </Button>
                      );
                    })}
                  </div>
                  {!showCustomAmount ? (
                    <Button
                      type="button"
                      variant="link"
                      className="h-auto w-full p-0 text-muted-foreground underline"
                      aria-expanded={showCustomAmount}
                      aria-controls="custom-topup-fields"
                      onClick={() => {
                        setSelectedBundleId(null);
                        setShowCustomAmount(true);
                      }}
                      disabled={walletCheckoutMutation.isPending}
                    >
                      Enter a custom amount
                    </Button>
                  ) : null}
                  {showCustomAmount ? (
                    <div id="custom-topup-fields" className="space-y-3">
                      <Label htmlFor="custom-topup-amount">Amount (USD)</Label>
                      <Input
                        id="custom-topup-amount"
                        className="focus-visible:ring-0 focus-visible:ring-offset-0"
                        type="number"
                        inputMode="decimal"
                        min="10"
                        max="999999.99"
                        step="0.01"
                        placeholder="Enter amount"
                        value={customAmount}
                        onChange={(event) => setCustomAmount(event.target.value)}
                        aria-describedby="custom-topup-help"
                        aria-invalid={customAmount !== "" && !customAmountValid}
                        disabled={walletCheckoutMutation.isPending}
                        required
                        autoFocus
                      />
                      <p id="custom-topup-help" className="text-xs text-muted-foreground" aria-live="polite">
                        {customAmountCredits !== null
                          ? `${formatMoney(customAmountCents)} for ${formatNumber(customAmountCredits)} credits`
                          : customAmount && !customAmountValid
                            ? "Enter an amount from $10 to $999,999.99, with up to two decimal places."
                            : "Minimum $10. Credits use the same rates as the top-up options above."}
                      </p>
                    </div>
                  ) : null}
                  {selectedBundle || showCustomAmount ? (
                    <div>
                      <Button
                        type="submit"
                        className="w-full"
                        disabled={!walletCheckoutRequest || walletCheckoutMutation.isPending}
                      >
                        {walletCheckoutMutation.isPending ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <CreditCard />
                        )}
                        Confirm top-up
                      </Button>
                    </div>
                  ) : null}
                </form>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
