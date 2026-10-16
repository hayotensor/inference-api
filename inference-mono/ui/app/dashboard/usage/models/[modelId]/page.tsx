"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BarChart3 } from "lucide-react";

import { AdminPagination } from "@/components/admin-common";
import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { StatusBadge } from "@/components/status-badge";
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
import { formatDateTime, formatMultiplier, formatNumber } from "@/lib/format";

const EVENTS_PAGE_SIZE = 25;

export default function UsageByModelDetailPage() {
  const params = useParams<{ modelId: string }>();
  const model = decodeURIComponent(params.modelId);
  const [offset, setOffset] = React.useState(0);

  const detailQuery = useQuery({
    queryKey: ["usage-by-model", model, { limit: EVENTS_PAGE_SIZE, offset }],
    queryFn: ({ signal }) =>
      usageApi.byModelDetail(model, { limit: EVENTS_PAGE_SIZE, offset }, { signal })
  });

  const summary = detailQuery.data?.summary;
  const events = detailQuery.data?.events ?? [];
  // Events are returned most-recent-first, so the first event's stored
  // multiplier is this model's current credit rate (it's on the wire per
  // event, not on the aggregate summary).
  const rateLabel = events.length > 0 ? formatMultiplier(events[0].model_multiplier) : null;

  return (
    <>
      <DashboardPageHeader
        title={model}
        description="Raw token usage, this model's credit rate, and recent requests. Credits charged is what actually left your allowance."
        action={
          <Button asChild variant="outline">
            <Link href="/dashboard/usage">
              <ArrowLeft />
              Back to usage
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
          />
          <StatCard
            label="Prompt tokens"
            value={formatNumber(summary.prompt_tokens)}
            icon={BarChart3}
          />
          <StatCard
            label="Completion tokens"
            value={formatNumber(summary.completion_tokens)}
            icon={BarChart3}
          />
          <StatCard
            label="Credits charged"
            value={formatNumber(summary.charged_tokens)}
            detail={
              rateLabel
                ? `${rateLabel} rate${summary.last_used_at ? ` · Last used ${formatDateTime(summary.last_used_at)}` : ""}`
                : summary.last_used_at
                  ? `Last used ${formatDateTime(summary.last_used_at)}`
                  : undefined
            }
            icon={BarChart3}
          />
        </div>
      ) : null}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Recent requests</CardTitle>
          <CardDescription>
            Most recent inference usage events for this model. Rate is the credit
            multiplier applied to that request&apos;s raw tokens.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {events.length === 0 && detailQuery.isSuccess ? (
            <EmptyState
              title="No usage yet"
              description="Requests to this model will appear here."
            />
          ) : null}
          {events.length > 0 ? (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Request</TableHead>
                    <TableHead>Prompt tokens</TableHead>
                    <TableHead>Completion tokens</TableHead>
                    <TableHead>Rate</TableHead>
                    <TableHead>Credits charged</TableHead>
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
                      <TableCell className="text-muted-foreground">
                        {formatMultiplier(event.model_multiplier)}
                      </TableCell>
                      <TableCell>{formatNumber(event.charged_tokens)}</TableCell>
                      <TableCell><StatusBadge status={event.status} /></TableCell>
                      <TableCell>{formatDateTime(event.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <AdminPagination
                total={detailQuery.data?.total_events ?? events.length}
                limit={detailQuery.data?.limit ?? EVENTS_PAGE_SIZE}
                offset={detailQuery.data?.offset ?? offset}
                onOffsetChange={setOffset}
              />
            </>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
