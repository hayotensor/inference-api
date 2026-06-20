"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { billingApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";

export default function BillingSuccessPage() {
  const { isAuthenticated } = useAuth();
  const subscriptionQuery = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: billingApi.subscription,
    enabled: isAuthenticated
  });

  return (
    <div className="min-h-screen bg-muted/30">
      <PublicNav />
      <main className="page-shell flex min-h-[calc(100vh-4rem)] items-center justify-center py-10">
        <Card className="w-full max-w-xl">
          <CardContent className="p-6 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
            <h1 className="mt-4 text-2xl font-semibold tracking-normal">Billing updated</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Stripe redirected back successfully. Subscription state is refetched from the API.
            </p>
            {isAuthenticated && subscriptionQuery.isLoading ? (
              <LoadingState />
            ) : null}
            {isAuthenticated && subscriptionQuery.isError ? (
              <ErrorState error={subscriptionQuery.error} />
            ) : null}
            {subscriptionQuery.data ? (
              <div className="mt-6 rounded-lg border bg-muted/40 p-4 text-left text-sm">
                <div className="flex justify-between gap-4">
                  <span className="text-muted-foreground">Status</span>
                  <span className="font-medium">{subscriptionQuery.data.status}</span>
                </div>
                <div className="mt-2 flex justify-between gap-4">
                  <span className="text-muted-foreground">Plan</span>
                  <span className="font-medium">{subscriptionQuery.data.plan_name ?? "None"}</span>
                </div>
                <div className="mt-2 flex justify-between gap-4">
                  <span className="text-muted-foreground">Period end</span>
                  <span className="font-medium">
                    {formatDate(subscriptionQuery.data.current_period_end)}
                  </span>
                </div>
              </div>
            ) : null}
            <Button className="mt-6" asChild>
              <Link href="/dashboard/billing">Open billing</Link>
            </Button>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
