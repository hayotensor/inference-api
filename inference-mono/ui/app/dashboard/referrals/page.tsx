"use client";

import { useQuery } from "@tanstack/react-query";
import { Clipboard, Gift, RefreshCw, Users } from "lucide-react";
import { toast } from "sonner";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { referralsApi } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

export default function ReferralsPage() {
  const summaryQuery = useQuery({
    queryKey: ["referrals-me"],
    queryFn: referralsApi.me
  });

  const summary = summaryQuery.data;

  return (
    <>
      <DashboardPageHeader
        title="Referrals"
        description="Share your link. When someone you referred pays for a plan or tops up their wallet, you earn 10% of that payment back as bonus inference-token credits, not cash."
        action={
          <Button variant="outline" onClick={() => void summaryQuery.refetch()}>
            <RefreshCw />
            Refresh
          </Button>
        }
      />

      {summaryQuery.isLoading ? <LoadingState /> : null}
      {summaryQuery.isError ? (
        <ErrorState error={summaryQuery.error} onRetry={() => void summaryQuery.refetch()} />
      ) : null}

      {summary ? (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <StatCard
              label="Referrals"
              value={formatNumber(summary.total_referrals)}
              icon={Users}
              detail="People who signed up through your link"
            />
            <StatCard
              label="Tokens earned"
              value={formatNumber(summary.total_tokens_earned)}
              icon={Gift}
              detail="Lifetime bonus credits added to your wallet"
              emphasis
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Your referral link</CardTitle>
              <CardDescription>
                Anyone who signs up through this link is permanently attributed to you. For the
                first 12 months after they sign up, 10% of what they pay Stripe (subscription or
                wallet top-up) is credited to your wallet as bonus tokens, converted at their own
                plan&apos;s effective $/token rate.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input readOnly value={summary.referral_link} className="font-mono text-sm" />
                <Button
                  onClick={() => {
                    void navigator.clipboard.writeText(summary.referral_link);
                    toast.success("Copied referral link");
                  }}
                >
                  <Clipboard />
                  Copy link
                </Button>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">
                Referral code: <span className="font-mono">{summary.referral_code}</span>
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Earnings history</CardTitle>
              <CardDescription>The most recent bonus-token credits your referrals earned you.</CardDescription>
            </CardHeader>
            <CardContent>
              {summary.recent_earnings.length === 0 ? (
                <EmptyState
                  title="No earnings yet"
                  description="Once someone who signed up through your link pays for a plan or tops up their wallet, the bonus tokens show up here."
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>Tokens credited</TableHead>
                      <TableHead className="text-right">Bonus value</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.recent_earnings.map((earning) => (
                      <TableRow key={earning.id}>
                        <TableCell>{formatDateTime(earning.created_at)}</TableCell>
                        <TableCell className="font-medium">
                          {formatNumber(earning.tokens_credited)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          ${(earning.usd_equivalent_cents / 100).toFixed(2)} (10% of their payment)
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}
    </>
  );
}
