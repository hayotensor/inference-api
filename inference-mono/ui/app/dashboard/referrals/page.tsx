"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clipboard, Gift, Pencil, RefreshCw, Users } from "lucide-react";
import { toast } from "sonner";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { StatCard } from "@/components/stat-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { apiErrorMessage, referralsApi } from "@/lib/api";
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
        description="Share your link. When someone you referred pays for a plan or tops up their wallet, you earn 10% of that payment back as bonus credits."
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
              label="Credits earned"
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
                first 12 months after they sign up, 10% of what they pay (subscription or
                wallet top-up) is credited to your wallet as bonus credits.
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
              <CustomCodeForm currentCode={summary.referral_code} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Earnings history</CardTitle>
              <CardDescription>The most recent credits your referrals earned you.</CardDescription>
            </CardHeader>
            <CardContent>
              {summary.recent_earnings.length === 0 ? (
                <EmptyState
                  title="No earnings yet"
                  description="Once someone who signed up through your link pays for a plan or tops up their wallet, the bonus credits show up here."
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>Credits rewarded</TableHead>
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

// HYP-93: must match the backend rules in app/referrals/service.py; the API
// is the source of truth (it also rejects reserved words and taken codes).
const CUSTOM_CODE_PATTERN = /^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/;

function customCodeProblem(code: string) {
  if (code.length < 3 || code.length > 32) return "Use 3-32 characters.";
  if (!CUSTOM_CODE_PATTERN.test(code)) {
    return "Use letters, numbers, - and _, starting and ending with a letter or number.";
  }
  return null;
}

function CustomCodeForm({ currentCode }: { currentCode: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const normalized = draft.trim().toLowerCase();
  const problem = draft ? customCodeProblem(normalized) : null;

  const mutation = useMutation({
    mutationFn: referralsApi.setCode,
    onSuccess: (summary) => {
      queryClient.setQueryData(["referrals-me"], summary);
      setEditing(false);
      toast.success("Referral link updated. Your old link still works.");
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  if (!editing) {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>
          Referral code: <span className="font-mono text-foreground">{currentCode}</span>
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setDraft(currentCode.toLowerCase());
            setEditing(true);
          }}
        >
          <Pencil />
          Customize
        </Button>
      </div>
    );
  }

  return (
    <form
      className="mt-4 space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!problem && normalized) mutation.mutate({ code: normalized });
      }}
    >
      <Label htmlFor="referral-code">Custom referral code</Label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="flex flex-1 items-center rounded-md border border-input bg-background pl-3 focus-within:ring-2 focus-within:ring-ring">
          <span className="font-mono text-sm text-muted-foreground">/r/</span>
          <Input
            id="referral-code"
            autoFocus
            maxLength={32}
            autoComplete="off"
            spellCheck={false}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="border-0 pl-0.5 font-mono text-sm shadow-none focus-visible:ring-0"
            aria-invalid={Boolean(problem)}
            aria-describedby="referral-code-help"
          />
        </div>
        <Button type="submit" disabled={!normalized || Boolean(problem) || mutation.isPending}>
          Save
        </Button>
        <Button type="button" variant="outline" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
      <p
        id="referral-code-help"
        className={problem ? "text-sm text-destructive" : "text-sm text-muted-foreground"}
      >
        {problem ??
          "3-32 characters: letters, numbers, - and _. Codes aren't case-sensitive. Links with your current code keep working after you change it."}
      </p>
    </form>
  );
}
