"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { apiErrorMessage, usageApi } from "@/lib/api";
import { formatDate, formatDateTime, formatNumber, formatPercent } from "@/lib/format";

export default function UsagePage() {
  const queryClient = useQueryClient();
  const currentQuery = useQuery({
    queryKey: ["usage-current"],
    queryFn: usageApi.current
  });
  const allowanceQuery = useQuery({
    queryKey: ["usage-allowance"],
    queryFn: usageApi.allowance
  });
  const historyQuery = useQuery({
    queryKey: ["usage-history"],
    queryFn: ({ signal }) => usageApi.history(100, { signal })
  });
  const recalculateMutation = useMutation({
    mutationFn: usageApi.recalculate,
    onSuccess: async () => {
      toast.success("Usage recalculated");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["usage-current"] }),
        queryClient.invalidateQueries({ queryKey: ["usage-allowance"] }),
        queryClient.invalidateQueries({ queryKey: ["usage-history"] })
      ]);
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const usage = currentQuery.data;
  const percent = usage
    ? formatPercent(usage.used_tokens, usage.total_allowance)
    : 0;

  return (
    <>
      <DashboardPageHeader
        title="Usage"
        description="Allowance, usage, and history are authoritative backend responses."
        action={
          <Button
            onClick={() => recalculateMutation.mutate(false)}
            disabled={recalculateMutation.isPending}
          >
            {recalculateMutation.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <RefreshCw />
            )}
            Recalculate
          </Button>
        }
      />

      {currentQuery.isLoading ? <LoadingState /> : null}
      {currentQuery.isError ? (
        <ErrorState error={currentQuery.error} onRetry={() => void currentQuery.refetch()} />
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          label="Total allowance"
          value={formatNumber(usage?.total_allowance)}
          detail={usage ? `${formatDate(usage.period_start)} to ${formatDate(usage.period_end)}` : undefined}
          icon={BarChart3}
          tone="blue"
        />
        <StatCard
          label="Used tokens"
          value={formatNumber(usage?.used_tokens)}
          detail={`${percent}% used`}
          icon={BarChart3}
          tone="amber"
        />
        <StatCard
          label="Remaining tokens"
          value={formatNumber(usage?.remaining_tokens)}
          icon={BarChart3}
          tone="green"
        />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Allowance breakdown</CardTitle>
            <CardDescription>Displayed without client-side recomputation.</CardDescription>
          </CardHeader>
          <CardContent>
            {allowanceQuery.isLoading ? <LoadingState /> : null}
            {allowanceQuery.isError ? <ErrorState error={allowanceQuery.error} /> : null}
            {allowanceQuery.data ? (
              <div className="space-y-4">
                <Progress
                  value={formatPercent(
                    allowanceQuery.data.used_tokens,
                    allowanceQuery.data.total_monthly_allowance
                  )}
                />
                {[
                  ["Stripe allowance", allowanceQuery.data.stripe_monthly_allowance],
                  [
                    "Ethereum ERC20 allowance",
                    allowanceQuery.data.ethereum_erc20_balance_allowance
                  ],
                  [
                    "Substrate native allowance",
                    allowanceQuery.data.substrate_evm_native_balance_allowance
                  ],
                  ["Manual allowance", allowanceQuery.data.manual_adjustments],
                  ["Total allowance", allowanceQuery.data.total_monthly_allowance],
                  ["Used tokens", allowanceQuery.data.used_tokens],
                  ["Remaining tokens", allowanceQuery.data.remaining_tokens]
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 text-sm">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium">{formatNumber(value)}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Usage history</CardTitle>
            <CardDescription>Most recent inference usage events.</CardDescription>
          </CardHeader>
          <CardContent>
            {historyQuery.isLoading ? <LoadingState /> : null}
            {historyQuery.isError ? <ErrorState error={historyQuery.error} /> : null}
            {historyQuery.data?.data.length === 0 ? (
              <EmptyState
                title="No usage yet"
                description="Inference calls made with API keys will appear here."
              />
            ) : null}
            {historyQuery.data && historyQuery.data.data.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Request</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Charged</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {historyQuery.data.data.map((event) => (
                    <TableRow key={event.id}>
                      <TableCell className="font-mono text-xs">{event.request_id}</TableCell>
                      <TableCell>{event.model}</TableCell>
                      <TableCell>{formatNumber(event.charged_tokens)}</TableCell>
                      <TableCell>{event.status}</TableCell>
                      <TableCell>{formatDateTime(event.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
