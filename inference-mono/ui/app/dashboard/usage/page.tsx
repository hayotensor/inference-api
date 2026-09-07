"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Eye, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { AdminPagination } from "@/components/admin-common";
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

const MODELS_PAGE_SIZE = 20;

export default function UsagePage() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = React.useState(0);

  const currentQuery = useQuery({
    queryKey: ["usage-current"],
    queryFn: usageApi.current
  });
  const allowanceQuery = useQuery({
    queryKey: ["usage-allowance"],
    queryFn: usageApi.allowance
  });
  const byModelQuery = useQuery({
    queryKey: ["usage-by-model", { limit: MODELS_PAGE_SIZE, offset }],
    queryFn: ({ signal }) =>
      usageApi.byModel({ limit: MODELS_PAGE_SIZE, offset }, { signal })
  });
  const recalculateMutation = useMutation({
    mutationFn: usageApi.recalculate,
    onSuccess: async () => {
      toast.success("Usage recalculated");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["usage-current"] }),
        queryClient.invalidateQueries({ queryKey: ["usage-allowance"] }),
        queryClient.invalidateQueries({ queryKey: ["usage-by-model"] })
      ]);
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const usage = currentQuery.data;
  const percent = usage
    ? formatPercent(usage.used_tokens, usage.total_allowance)
    : 0;
  const rows = byModelQuery.data?.data ?? [];

  return (
    <>
      <DashboardPageHeader
        title="Usage"
        description="Allowance and per-model token usage are authoritative backend responses."
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
        />
        <StatCard
          label="Used tokens"
          value={formatNumber(usage?.used_tokens)}
          detail={`${percent}% used`}
          icon={BarChart3}
        />
        <StatCard
          label="Remaining tokens"
          value={formatNumber(usage?.remaining_tokens)}
          icon={BarChart3}
        />
      </div>

      <Card className="mt-6">
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
              <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
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
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Usage by model</CardTitle>
          <CardDescription>
            One row per model. Token totals are summed across every request for that
            model. Select a model to see its recent requests.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {byModelQuery.isLoading ? <LoadingState /> : null}
          {byModelQuery.isError ? (
            <ErrorState error={byModelQuery.error} onRetry={() => void byModelQuery.refetch()} />
          ) : null}
          {byModelQuery.data && rows.length === 0 ? (
            <EmptyState
              title="No usage yet"
              description="Inference calls made with API keys will appear here, grouped by model."
            />
          ) : null}
          {rows.length > 0 ? (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead>Requests</TableHead>
                    <TableHead>Prompt tokens</TableHead>
                    <TableHead>Completion tokens</TableHead>
                    <TableHead>Charged tokens</TableHead>
                    <TableHead>Last used</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.model}>
                      <TableCell className="font-medium">
                        <Link
                          href={`/dashboard/usage/models/${encodeURIComponent(row.model)}`}
                          className="hover:underline"
                        >
                          {row.model}
                        </Link>
                      </TableCell>
                      <TableCell>{formatNumber(row.request_count)}</TableCell>
                      <TableCell>{formatNumber(row.prompt_tokens)}</TableCell>
                      <TableCell>{formatNumber(row.completion_tokens)}</TableCell>
                      <TableCell>{formatNumber(row.charged_tokens)}</TableCell>
                      <TableCell>{formatDateTime(row.last_used_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button asChild variant="outline" size="sm">
                          <Link href={`/dashboard/usage/models/${encodeURIComponent(row.model)}`}>
                            <Eye />
                            View
                          </Link>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <AdminPagination
                total={byModelQuery.data?.total ?? rows.length}
                limit={byModelQuery.data?.limit ?? MODELS_PAGE_SIZE}
                offset={byModelQuery.data?.offset ?? offset}
                onOffsetChange={setOffset}
              />
            </>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
