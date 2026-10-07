"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, CreditCard, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { BillingIntervalToggle } from "@/components/billing-interval-toggle";
import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { apiErrorMessage, billingApi, redirectToStripeSession } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { planTierCopy } from "@/lib/plan-features";
import { formatMoney, formatNumber, formatRatePerMillion } from "@/lib/format";
import {
  annualSavingsPercent,
  isStripeTestMode,
  monthlyEquivalentCents,
  planIsFree,
  planIsPurchasable,
  planPriceCents
} from "@/lib/stripe";
import type { BillingInterval, BillingPlan } from "@/lib/types";

export default function PricingPage() {
  const { isAuthenticated } = useAuth();
  const [interval, setInterval] = React.useState<BillingInterval>("month");
  const plansQuery = useQuery({
    queryKey: ["billing-plans"],
    queryFn: billingApi.plans
  });

  const checkoutMutation = useMutation({
    mutationFn: ({ planId, interval: checkoutInterval }: { planId: string; interval: BillingInterval }) =>
      billingApi.checkout(planId, checkoutInterval),
    onSuccess: redirectToStripeSession,
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const plans = (plansQuery.data ?? []).filter((plan) => plan.active);
  // Any plan may set the headline discount; they are all 2-months-free today.
  const savingsPercent = plans.reduce<number | null>(
    (best, plan) => Math.max(best ?? 0, annualSavingsPercent(plan) ?? 0) || null,
    null
  );
  const annualAvailable = plans.some((plan) => planIsPurchasable(plan, "year"));

  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main className="page-shell py-12">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl">
            {isStripeTestMode ? <Badge variant="warning">Stripe test mode</Badge> : null}
            <h1 className="mt-4 text-3xl font-semibold tracking-normal sm:text-4xl">
              Pricing
            </h1>
            <p className="mt-3 text-muted-foreground">
              Every plan includes a monthly credit allowance across all served models. Credits
              are spent per request, and each model has its own rate -- larger models cost more
              credits per token than smaller ones. Cancel or change plans at any time from the
              billing dashboard.
            </p>
          </div>
          {annualAvailable ? (
            <BillingIntervalToggle
              value={interval}
              onChange={setInterval}
              savingsPercent={savingsPercent}
            />
          ) : null}
        </div>

        {plansQuery.isLoading ? <LoadingState /> : null}
        {plansQuery.isError ? (
          <ErrorState error={plansQuery.error} onRetry={() => void plansQuery.refetch()} />
        ) : null}

        {plansQuery.data ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            {plans.map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                interval={interval}
                isAuthenticated={isAuthenticated}
                checkoutPending={checkoutMutation.isPending}
                onCheckout={() => checkoutMutation.mutate({ planId: plan.id, interval })}
              />
            ))}
          </div>
        ) : null}

        <p className="mt-8 text-xs text-muted-foreground">
          Prices are billed in USD by Stripe. Annual plans are billed once for the year.
        </p>
      </main>
      <SiteFooter />
    </div>
  );
}

function PlanCard({
  plan,
  interval,
  isAuthenticated,
  checkoutPending,
  onCheckout
}: {
  plan: BillingPlan;
  interval: BillingInterval;
  isAuthenticated: boolean;
  checkoutPending: boolean;
  onCheckout: () => void;
}) {
  const isFree = planIsFree(plan);
  const priceCents = isFree ? 0 : planPriceCents(plan, interval);
  const purchasable = planIsPurchasable(plan, interval);
  const monthlyEquivalent = interval === "year" ? monthlyEquivalentCents(plan) : null;
  const rate = formatRatePerMillion(
    plan.price_cents,
    plan.monthly_token_allowance,
    plan.currency,
    "credits"
  );

  const tier = planTierCopy(plan);
  const creditsLine = plan.is_unlimited
    ? "Unlimited credits"
    : `${formatNumber(plan.monthly_token_allowance)} credits / month`;
  const features = tier
    ? [...(tier.inherits ? [`Everything in ${tier.inherits}`] : []), creditsLine, ...tier.adds]
    : [
        creditsLine,
        "OpenAI-compatible API + usage dashboard",
        `${String(plan.features?.support ?? "community")} support`
      ];

  return (
    <Card className={plan.name === "Pro" ? "border-primary" : ""}>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>{plan.name}</CardTitle>
          {plan.name === "Pro" ? <Badge>Popular</Badge> : null}
        </div>
        <CardDescription>
          {plan.is_unlimited ? "Unlimited credits" : `${formatNumber(plan.monthly_token_allowance)} credits per month`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div>
          <div className="text-3xl font-semibold tracking-normal">
            {isFree || purchasable ? formatMoney(priceCents, plan.currency) : "Unavailable"}
            {isFree || purchasable ? (
              <span className="text-sm font-normal text-muted-foreground">
                {interval === "year" && !isFree ? " / year" : " / month"}
              </span>
            ) : null}
          </div>
          <p className="mt-1 h-4 text-xs text-muted-foreground">
            {monthlyEquivalent && purchasable
              ? `${formatMoney(monthlyEquivalent, plan.currency)} / month, billed yearly`
              : rate && !isFree
                ? rate
                : ""}
          </p>
        </div>

        <ul className="space-y-2 text-sm text-muted-foreground">
          {features.map((feature) => (
            <li key={feature} className="flex gap-2">
              <Check className="mt-0.5 h-4 w-4 shrink-0 text-foreground" strokeWidth={1.5} />
              {feature}
            </li>
          ))}
        </ul>

        {isFree ? (
          <Button className="w-full" variant="outline" asChild>
            <Link href={isAuthenticated ? "/dashboard" : "/signup"}>
              {isAuthenticated ? "Open dashboard" : "Start free"}
              <ArrowRight />
            </Link>
          </Button>
        ) : !purchasable ? (
          <Button className="w-full" variant="outline" disabled>
            {interval === "year" ? "No annual price" : "Not available"}
          </Button>
        ) : isAuthenticated ? (
          <Button className="w-full" onClick={onCheckout} disabled={checkoutPending}>
            {checkoutPending ? <Loader2 className="animate-spin" /> : <CreditCard />}
            Choose {plan.name}
          </Button>
        ) : (
          <Button className="w-full" asChild>
            <Link href={`/login?next=${encodeURIComponent("/pricing")}`}>
              Log in to choose
              <ArrowRight />
            </Link>
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
