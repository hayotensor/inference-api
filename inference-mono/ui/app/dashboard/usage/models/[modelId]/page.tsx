"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BarChart3 } from "lucide-react";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

export default function UsageByModelDetailPage() {
  const params = useParams<{ modelId: string }>();
  const model = decodeURIComponent(params.modelId);

  const detailQuery = useQuery({
    queryKey: ["usage-by-model", model],
    queryFn: ({ signal }) => usageApi.byModelDetail(model, 100, { signal })
  });

  const summary = detailQuery.data?.summary;
  const events = detailQuery.data?.events ?? [];

  return (
    <>
      <DashboardPageHeader
        title={model}
        description="Token usage totals and recent requests for this model."
        action={
          <Button asChild variant="outline">
            <Link href="/dashboard/usage/models">
              <ArrowLeft />
              Back to models
            </Link>
          </Button>
        }
      />

      {detailQuery.isLoading ? <LoadingState /> : null}
      {detailQuery.isError ? (
        <ErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />
      ) : null}

      {summary ? (
        <div className="grid gap-4 md:grid-cols-4">
          <StatCard
            label="Requests"
            value={formatNumber(summary.request_count)}
            icon={BarChart3}
            tone="blue"
          />
          <StatCard
            label="Prompt tokens"
            value={formatNumber(summary.prompt_tokens)}
            icon={BarChart3}
            tone="amber"
          />
          <StatCard
            label="Completion tokens"
            value={formatNumber(summary.completion_tokens)}
            icon={BarChart3}
            tone="amber"
          />
          <StatCard
            label="Charged tokens"
            value={formatNumber(summary.charged_tokens)}
            detail={summary.last_used_at ? `Last used ${formatDateTime(summary.last_used_at)}` : undefined}
            icon={BarChart3}
            tone="green"
          />
        </div>
      ) : null}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Recent requests</CardTitle>
          <CardDescription>Most recent inference usage events for this model.</CardDescription>
        </CardHeader>
        <CardContent>
          {events.length === 0 && detailQuery.isSuccess ? (
            <EmptyState
              title="No usage yet"
              description="Requests to this model will appear here."
            />
          ) : null}
          {events.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Request</TableHead>
                  <TableHead>Prompt tokens</TableHead>
                  <TableHead>Completion tokens</TableHead>
                  <TableHead>Charged</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="font-mono text-xs">{event.request_id}</TableCell>
                    <TableCell>{formatNumber(event.prompt_tokens)}</TableCell>
                    <TableCell>{formatNumber(event.completion_tokens)}</TableCell>
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
    </>
  );
}
