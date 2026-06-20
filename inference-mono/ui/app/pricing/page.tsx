"use client";

import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
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
import { formatNumber } from "@/lib/format";

export default function PricingPage() {
  const { isAuthenticated } = useAuth();
  const plansQuery = useQuery({
    queryKey: ["billing-plans"],
    queryFn: billingApi.plans
  });

  const checkoutMutation = useMutation({
    mutationFn: billingApi.checkout,
    onSuccess: redirectToStripeSession,
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <div className="min-h-screen bg-background">
      <PublicNav />
      <main className="page-shell py-12">
        <div className="mb-8 max-w-2xl">
          <Badge variant="secondary">Backend-owned Stripe</Badge>
          <h1 className="mt-4 text-3xl font-semibold tracking-normal sm:text-4xl">
            Pricing
          </h1>
          <p className="mt-3 text-muted-foreground">
            Plans and token allowances are loaded from the FastAPI billing API.
          </p>
        </div>

        {plansQuery.isLoading ? <LoadingState /> : null}
        {plansQuery.isError ? (
          <ErrorState error={plansQuery.error} onRetry={() => void plansQuery.refetch()} />
        ) : null}

        {plansQuery.data ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {plansQuery.data
              .filter((plan) => plan.active)
              .map((plan) => {
                const isFree = !plan.stripe_price_id;
                return (
                  <Card key={plan.id} className={plan.name === "Pro" ? "border-primary" : ""}>
                    <CardHeader>
                      <div className="flex items-center justify-between gap-3">
                        <CardTitle>{plan.name}</CardTitle>
                        {plan.name === "Pro" ? <Badge>Popular</Badge> : null}
                      </div>
                      <CardDescription>
                        {formatNumber(plan.monthly_token_allowance)} tokens per month
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                      <div className="text-3xl font-semibold tracking-normal">
                        {isFree ? "$0" : "Stripe"}
                        <span className="text-sm font-normal text-muted-foreground">
                          {isFree ? " / month" : " checkout"}
                        </span>
                      </div>
                      <ul className="space-y-2 text-sm text-muted-foreground">
                        <li className="flex gap-2">
                          <Check className="mt-0.5 h-4 w-4 text-emerald-600" />
                          Allowance returned by `/billing/plans`
                        </li>
                        <li className="flex gap-2">
                          <Check className="mt-0.5 h-4 w-4 text-emerald-600" />
                          Subscription state owned by the API
                        </li>
                        <li className="flex gap-2">
                          <Check className="mt-0.5 h-4 w-4 text-emerald-600" />
                          Checkout URL returned by FastAPI
                        </li>
                      </ul>
                      {isFree ? (
                        <Button className="w-full" variant="outline" asChild>
                          <Link href={isAuthenticated ? "/dashboard" : "/signup"}>
                            {isAuthenticated ? "Open dashboard" : "Start free"}
                            <ArrowRight />
                          </Link>
                        </Button>
                      ) : isAuthenticated ? (
                        <Button
                          className="w-full"
                          onClick={() => checkoutMutation.mutate(plan.id)}
                          disabled={checkoutMutation.isPending}
                        >
                          {checkoutMutation.isPending ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <CreditCardIcon />
                          )}
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
              })}
          </div>
        ) : null}
      </main>
    </div>
  );
}

function CreditCardIcon() {
  return <span className="h-4 w-4 rounded-sm border border-current" aria-hidden />;
}
