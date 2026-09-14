"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleCheck, CircleSlash, TriangleAlert } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { ModelServiceStatus, ModelStatusEntry, StatusResponse } from "@/lib/types";

// HYP-58: live per-model status page (see openrouter's status.openrouter.ai
// for the pattern this follows). Deliberately a SNAPSHOT view only -- current
// miner availability per model, refetched on an interval -- not a historical
// uptime/incident-timeline system; see app/api/status/route.ts and
// inference_api/status_routes.py for what's actually being measured and why
// a richer history is out of scope for this first pass.
//
// The brand kit has no status-color hue ramp (see components/ui/badge.tsx's
// header comment: variants are a *weight* ladder, not red/green/amber), so
// this reuses that same ladder instead of inventing traffic-light colors:
// operational -> `success` (the ladder's one affirmative/filled state),
// degraded -> `warning`, down -> `destructive` (heaviest border = most
// attention). Each also gets a distinct icon SHAPE so status never depends on
// color alone.

const STATUS_COPY: Record<ModelServiceStatus, { label: string; badge: BadgeProps["variant"] }> = {
  operational: { label: "Operational", badge: "success" },
  degraded: { label: "Degraded", badge: "warning" },
  down: { label: "Down", badge: "destructive" }
};

function StatusIcon({ status, className }: { status: ModelServiceStatus; className?: string }) {
  if (status === "operational") return <CircleCheck className={className} strokeWidth={1.5} />;
  if (status === "degraded") return <TriangleAlert className={className} strokeWidth={1.5} />;
  return <CircleSlash className={className} strokeWidth={1.5} />;
}

const OVERALL_COPY: Record<ModelServiceStatus, string> = {
  operational: "All systems operational",
  degraded: "Degraded performance",
  down: "Partial outage"
};

async function fetchStatus(): Promise<StatusResponse> {
  const res = await fetch("/api/status", { cache: "no-store" });
  if (!res.ok) throw new Error("Could not load status right now.");
  return (await res.json()) as StatusResponse;
}

export default function StatusPage() {
  const statusQuery = useQuery({
    queryKey: ["service-status"],
    queryFn: fetchStatus,
    // A status page should stay current without a manual reload -- 30s is
    // frequent enough to feel live without hammering the endpoint (which is
    // also IP-throttled server-side, see main.py's public-endpoint throttle).
    refetchInterval: 30_000
  });

  const data = statusQuery.data;

  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main className="page-shell py-12">
        <div className="max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-normal sm:text-4xl">Status</h1>
          <p className="mt-3 text-muted-foreground">
            Live availability for every model Talaris serves, derived from current miner
            heartbeats -- not a historical uptime log.
          </p>
        </div>

        {statusQuery.isLoading ? <LoadingState className="mt-8" /> : null}
        {statusQuery.isError ? (
          <ErrorState error={statusQuery.error} onRetry={() => void statusQuery.refetch()} />
        ) : null}

        {data ? (
          <>
            <Card className="mt-8">
              <CardContent className="flex flex-wrap items-center justify-between gap-4 py-5">
                <div className="flex items-center gap-3">
                  <StatusIcon status={data.overall_status} className="h-5 w-5 text-foreground" />
                  <span className="font-display text-lg font-medium tracking-display">
                    {OVERALL_COPY[data.overall_status]}
                  </span>
                </div>
                <Badge variant={STATUS_COPY[data.overall_status].badge}>
                  {STATUS_COPY[data.overall_status].label}
                </Badge>
              </CardContent>
            </Card>

            <div className="mt-4 space-y-2">
              {data.models.length === 0 ? (
                <Card>
                  <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    No models are registered with the platform yet.
                  </CardContent>
                </Card>
              ) : (
                data.models.map((model) => <ModelStatusRow key={model.model_id} model={model} />)
              )}
            </div>

            <p className="mt-6 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Activity className="h-3.5 w-3.5" strokeWidth={1.5} />
              Last checked {formatDateTime(data.generated_at)} -- refreshes automatically every 30
              seconds.
            </p>
          </>
        ) : null}
      </main>
    </div>
  );
}

function ModelStatusRow({ model }: { model: ModelStatusEntry }) {
  const copy = STATUS_COPY[model.status];
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
        <div className="flex items-center gap-3">
          <StatusIcon status={model.status} className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <div className="font-mono text-sm">{model.model_id}</div>
            <div className="text-xs text-muted-foreground">
              {model.healthy_miner_count} of {model.registered_miner_count || model.healthy_miner_count}{" "}
              miner{model.registered_miner_count === 1 ? "" : "s"} healthy
              {model.label ? ` -- ${model.label}` : ""}
            </div>
          </div>
        </div>
        <Badge variant={copy.badge}>{copy.label}</Badge>
      </CardContent>
    </Card>
  );
}
