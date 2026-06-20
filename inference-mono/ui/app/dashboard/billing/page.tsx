"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CreditCard, ExternalLink, Loader2, RefreshCw, XCircle } from "lucide-react";
import { toast } from "sonner";

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
import { formatDate, formatNumber } from "@/lib/format";

export default function BillingPage() {
  const queryClient = useQueryClient();
  const subscriptionQuery = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: billingApi.subscription
  });
  const plansQuery = useQuery({
    queryKey: ["billing-plans"],
    queryFn: billingApi.plans
  });

  const portalMutation = useMutation({
    mutationFn: billingApi.portal,
    onSuccess: redirectToStripeSession,
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const checkoutMutation = useMutation({
    mutationFn: billingApi.checkout,
    onSuccess: redirectToStripeSession,
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

  return (
    <>
      <DashboardPageHeader
        title="Billing"
        description="Stripe sessions and subscription state are created and synced by the backend."
        action={
          <Button variant="outline" onClick={() => void subscriptionQuery.refetch()}>
            <RefreshCw />
            Refresh
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Current subscription</CardTitle>
            <CardDescription>Raw subscription fields from `/billing/subscription`.</CardDescription>
          </CardHeader>
          <CardContent>
            {subscriptionQuery.isLoading ? <LoadingState /> : null}
            {subscriptionQuery.isError ? <ErrorState error={subscriptionQuery.error} /> : null}
            {subscriptionQuery.data ? (
              <div className="space-y-4 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Status</span>
                  <Badge variant={subscriptionQuery.data.status === "active" ? "success" : "outline"}>
                    {subscriptionQuery.data.status}
                  </Badge>
                </div>
                {[
                  ["Plan", subscriptionQuery.data.plan_name ?? "None"],
                  ["Stripe price", subscriptionQuery.data.stripe_price_id ?? "None"],
                  ["Period start", formatDate(subscriptionQuery.data.current_period_start)],
                  ["Period end", formatDate(subscriptionQuery.data.current_period_end)],
                  [
                    "Cancel at period end",
                    subscriptionQuery.data.cancel_at_period_end ? "Yes" : "No"
                  ]
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="max-w-[60%] truncate font-medium">{value}</span>
                  </div>
                ))}
                <div className="grid gap-2 pt-2 sm:grid-cols-2">
                  <Button
                    onClick={() => portalMutation.mutate()}
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
                      subscriptionQuery.data.status !== "active"
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
            <CardTitle>Plans</CardTitle>
            <CardDescription>Selecting a paid plan calls the API checkout endpoint.</CardDescription>
          </CardHeader>
          <CardContent>
            {plansQuery.isLoading ? <LoadingState /> : null}
            {plansQuery.isError ? <ErrorState error={plansQuery.error} /> : null}
            {plansQuery.data ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Plan</TableHead>
                    <TableHead>Allowance</TableHead>
                    <TableHead>Stripe</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plansQuery.data
                    .filter((plan) => plan.active)
                    .map((plan) => {
                      const current = subscriptionQuery.data?.plan_id === plan.id;
                      return (
                        <TableRow key={plan.id}>
                          <TableCell className="font-medium">{plan.name}</TableCell>
                          <TableCell>{formatNumber(plan.monthly_token_allowance)}</TableCell>
                          <TableCell>
                            {plan.stripe_price_id ? (
                              <Badge variant="outline">Paid</Badge>
                            ) : (
                              <Badge variant="secondary">Free</Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {current ? (
                              <Badge variant="success">Current</Badge>
                            ) : plan.stripe_price_id ? (
                              <Button
                                size="sm"
                                onClick={() => checkoutMutation.mutate(plan.id)}
                                disabled={checkoutMutation.isPending}
                              >
                                {checkoutMutation.isPending ? (
                                  <Loader2 className="animate-spin" />
                                ) : (
                                  <CreditCard />
                                )}
                                Choose
                              </Button>
                            ) : (
                              <span className="text-sm text-muted-foreground">Included</span>
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
      </div>
    </>
  );
}
