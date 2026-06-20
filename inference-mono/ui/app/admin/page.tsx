"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Activity, CreditCard, KeyRound, ListChecks, Shield, Users } from "lucide-react";

import { AdminPageHeader, hasPermission } from "@/components/admin-common";
import { useAdmin } from "@/components/admin-shell";
import { ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { adminApi } from "@/lib/api";
import { formatCompact, formatDateTime, formatNumber } from "@/lib/format";

export default function AdminOverviewPage() {
  const admin = useAdmin();
  const canStats = hasPermission(admin, "stats.read");
  const canAudit = hasPermission(admin, "audit.read");

  const overviewQuery = useQuery({
    queryKey: ["admin", "stats", "overview"],
    queryFn: adminApi.statsOverview,
    enabled: canStats
  });
  const usageQuery = useQuery({
    queryKey: ["admin", "stats", "usage"],
    queryFn: adminApi.statsUsage,
    enabled: canStats
  });
  const billingQuery = useQuery({
    queryKey: ["admin", "stats", "billing"],
    queryFn: adminApi.statsBilling,
    enabled: canStats
  });
  const auditQuery = useQuery({
    queryKey: ["admin", "audit-logs", "recent"],
    queryFn: ({ signal }) =>
      adminApi.auditLogs({ limit: 8, offset: 0, sort: "-created_at" }, { signal }),
    enabled: canAudit
  });

  return (
    <>
      <AdminPageHeader
        title="Admin overview"
        description="Operational status and recent administrative activity."
      />

      {!canStats ? (
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div>
              <h2 className="text-lg font-semibold tracking-normal">Admin access</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Your roles do not include platform stats. Use the available navigation items
                for your assigned admin tasks.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {admin.roles.map((role) => (
                <Badge key={role.id} variant="outline">
                  {role.name}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {canStats && overviewQuery.isLoading ? <LoadingState /> : null}
      {canStats && overviewQuery.isError ? <ErrorState error={overviewQuery.error} /> : null}

      {canStats && overviewQuery.data ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <StatCard
            label="Total users"
            value={formatNumber(overviewQuery.data.users_total)}
            icon={Users}
            detail={`${formatNumber(overviewQuery.data.users_active)} active`}
          />
          <StatCard
            label="Disabled users"
            value={formatNumber(overviewQuery.data.users_disabled)}
            icon={Shield}
            tone="amber"
          />
          <StatCard
            label="Active API keys"
            value={formatNumber(overviewQuery.data.active_api_keys)}
            icon={KeyRound}
            tone="green"
          />
          <StatCard
            label="Visible plans"
            value={formatNumber(overviewQuery.data.visible_plans)}
            icon={ListChecks}
            tone="neutral"
          />
          <StatCard
            label="Active subscriptions"
            value={formatNumber(overviewQuery.data.active_subscriptions)}
            icon={CreditCard}
            tone="green"
          />
          <StatCard
            label="Used tokens"
            value={formatCompact(usageQuery.data?.total_used_tokens)}
            icon={Activity}
            detail={`${formatCompact(usageQuery.data?.total_remaining_tokens)} remaining`}
          />
        </div>
      ) : null}

      {canStats && billingQuery.data ? (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Billing mix</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-4">
            {[
              ["Accounts", billingQuery.data.billing_accounts],
              ["Active", billingQuery.data.active_subscriptions],
              ["Trialing", billingQuery.data.trialing_subscriptions],
              ["Canceled", billingQuery.data.canceled_subscriptions]
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border bg-muted/30 p-3">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="mt-1 text-xl font-semibold tracking-normal">{formatNumber(value)}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {canAudit ? (
        <Card className="mt-4">
          <CardHeader className="flex flex-row items-center justify-between gap-4">
            <CardTitle>Recent admin activity</CardTitle>
            <Button asChild variant="outline" size="sm">
              <Link href="/admin/audit-logs">View all</Link>
            </Button>
          </CardHeader>
          <CardContent>
            {auditQuery.isLoading ? <LoadingState /> : null}
            {auditQuery.isError ? <ErrorState error={auditQuery.error} /> : null}
            {auditQuery.data ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Action</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead>Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {auditQuery.data.data.map((log) => (
                    <TableRow key={log.id}>
                      <TableCell className="font-medium">{log.action}</TableCell>
                      <TableCell>{log.target_type ?? "system"}</TableCell>
                      <TableCell>
                        <Badge variant={log.result === "success" ? "success" : "destructive"}>
                          {log.result}
                        </Badge>
                      </TableCell>
                      <TableCell>{formatDateTime(log.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </>
  );
}
