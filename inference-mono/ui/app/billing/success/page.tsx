"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";

import { ErrorState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { billingApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";

// Stripe's webhook can take a few seconds to reach us. `session_id` (appended to
// the success redirect) lets us confirm the payment directly instead of waiting
// on it; the interval below is a fallback for the rare case that fails too (or
// for anyone who lands here without a session_id) so the page still catches the
// webhook's own update instead of sitting on a stale "Free" forever.
const POLL_ATTEMPTS = 8;
const POLL_INTERVAL_MS = 2000;

export default function BillingSuccessPage() {
  return (
    <React.Suspense fallback={<BillingSuccessFallback />}>
      <BillingSuccessContent />
    </React.Suspense>
  );
}

function BillingSuccessFallback() {
  return (
    <div className="min-h-dvh bg-muted/30">
      <PublicNav />
      <main className="page-shell flex min-h-[calc(100dvh-4rem)] items-center justify-center py-10">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </main>
    </div>
  );
}

function BillingSuccessContent() {
  const { isAuthenticated } = useAuth();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session_id");
  const queryClient = useQueryClient();
  const syncStarted = React.useRef(false);
  const pollAttempts = React.useRef(0);

  const syncMutation = useMutation({
    mutationFn: (id: string) => billingApi.syncCheckoutSession(id),
    onSuccess: (data) => {
      // Written straight into the shared cache key so every page reading
      // ["billing-subscription"] — not just this one — reflects it immediately.
      queryClient.setQueryData(["billing-subscription"], data);
      void queryClient.invalidateQueries({ queryKey: ["usage-allowance"] });
    }
  });

  React.useEffect(() => {
    if (!isAuthenticated || !sessionId || syncStarted.current) return;
    syncStarted.current = true;
    syncMutation.mutate(sessionId);
    // syncMutation is a stable ref from useMutation; only re-run if the inputs change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, sessionId]);

  const subscriptionQuery = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: billingApi.subscription,
    enabled: isAuthenticated,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && status !== "free") return false;
      if (pollAttempts.current >= POLL_ATTEMPTS) return false;
      pollAttempts.current += 1;
      return POLL_INTERVAL_MS;
    }
  });

  const stillConfirming =
    isAuthenticated &&
    (syncMutation.isPending ||
      (subscriptionQuery.data?.status === "free" &&
        (subscriptionQuery.isFetching || pollAttempts.current < POLL_ATTEMPTS)));
  const gaveUpConfirming =
    isAuthenticated &&
    !stillConfirming &&
    subscriptionQuery.data?.status === "free" &&
    pollAttempts.current >= POLL_ATTEMPTS;

  return (
    <div className="min-h-dvh bg-muted/30">
      <PublicNav />
      <main className="page-shell flex min-h-[calc(100dvh-4rem)] items-center justify-center py-10">
        <Card className="w-full max-w-xl">
          <CardContent className="p-6 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-foreground" strokeWidth={1.25} />
            <h1 className="mt-4 text-2xl font-semibold tracking-normal">Billing updated</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Stripe redirected back successfully. Subscription state is refetched from the API.
            </p>

            {stillConfirming ? (
              <div className="mt-6 flex items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Confirming your payment with Stripe…
              </div>
            ) : null}

            {gaveUpConfirming ? (
              <div className="mt-6 rounded-lg border border-border-strong bg-elevated p-4 text-left text-sm leading-relaxed text-foreground">
                Stripe hasn&apos;t confirmed this payment with us yet. This can happen if the
                webhook is delayed. Try refreshing in a moment, or check the billing dashboard.
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-3"
                  onClick={() => {
                    pollAttempts.current = 0;
                    void subscriptionQuery.refetch();
                  }}
                >
                  Check again
                </Button>
              </div>
            ) : null}

            {isAuthenticated && subscriptionQuery.isError ? (
              <ErrorState error={subscriptionQuery.error} onRetry={() => void subscriptionQuery.refetch()} />
            ) : null}

            {subscriptionQuery.data && !stillConfirming ? (
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
