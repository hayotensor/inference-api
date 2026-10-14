"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, CreditCard, ExternalLink, Loader2, RefreshCw, Wallet, XCircle } from "lucide-react";
import { toast } from "sonner";

import { BillingIntervalToggle } from "@/components/billing-interval-toggle";
import { DashboardPageHeader } from "@/components/dashboard-shell";
import { ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { BillingInterval } from "@/lib/types";

export default function BillingPage() {
  const queryClient = useQueryClient();
  const [interval, setInterval] = React.useState<BillingInterval>("month");
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
      toast.success("Plan updated: Stripe will prorate the difference");
      await queryClient.invalidateQueries({ queryKey: ["billing-subscription"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-allowance"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const cancelMutation = useMutation({
    mutationFn: () => billingApi.cancel(true),
    onSuccess: async () => {
      toast.success("Cancellation scheduled");
      await queryClient.invalidateQueries({ queryKey: ["billing-subscription"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const walletCheckoutMutation = useMutation({
    mutationFn: (bundleId: string) => billingApi.walletCheckout(bundleId),
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
                  <Badge variant={subscription.status === "active" ? "success" : "outline"}>
                    {subscription.status}
                  </Badge>
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
                    disabled={cancelMutation.isPending || subscription.status !== "active"}
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
                    ? "Switching plans updates the subscription in place; The difference prorates."
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
                            <Badge variant="success">Current</Badge>
                          ) : isFree ? (
                            <span className="text-sm text-muted-foreground">Included</span>
                          ) : !purchasable ? (
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
                <div className="space-y-4">
                  <div className="my-4 border-t-2 border-gray-100"></div>
                  <span className="text-muted-foreground font-medium">Top up</span>
                  {walletBundlesQuery.isLoading ? <LoadingState /> : null}
                  {walletBundlesQuery.isError ? <ErrorState error={walletBundlesQuery.error} /> : null}
                  <div className="grid gap-2 sm:grid-cols-2">
                    {(walletBundlesQuery.data ?? []).map((bundle) => {
                      const isThisBundlePending =
                        walletCheckoutMutation.isPending &&
                        walletCheckoutMutation.variables === bundle.id;
                      return (
                        <Button
                          key={bundle.id}
                          variant="outline"
                          onClick={() => walletCheckoutMutation.mutate(bundle.id)}
                          disabled={walletCheckoutMutation.isPending}
                        >
                          {isThisBundlePending ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Wallet />
                          )}
                          {formatMoney(bundle.usd_cents, bundle.currency)} for{" "}
                          {formatNumber(bundle.tokens)}
                        </Button>
                      );
                    })}
                  </div>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
