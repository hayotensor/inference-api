"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Eye } from "lucide-react";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { usageApi } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

export default function UsageByModelPage() {
  const byModelQuery = useQuery({
    queryKey: ["usage-by-model"],
    queryFn: ({ signal }) => usageApi.byModel({ signal })
  });

  const rows = byModelQuery.data?.data ?? [];

  return (
    <>
      <DashboardPageHeader
        title="Usage by model"
        description="Token usage totals grouped by model. Select a model to see its recent requests."
        action={
          <Button asChild variant="outline">
            <Link href="/dashboard/usage">
              <ArrowLeft />
              Back to usage
            </Link>
          </Button>
        }
      />

      {byModelQuery.isLoading ? <LoadingState /> : null}
      {byModelQuery.isError ? (
        <ErrorState error={byModelQuery.error} onRetry={() => void byModelQuery.refetch()} />
      ) : null}
      {byModelQuery.data && rows.length === 0 ? (
        <EmptyState
          title="No usage yet"
          description="Inference calls made with API keys will appear here, broken down by model."
        />
      ) : null}
      {rows.length > 0 ? (
        <div className="rounded-lg border bg-background">
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
                  <TableCell className="font-medium">{row.model}</TableCell>
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
        </div>
      ) : null}
    </>
  );
}
