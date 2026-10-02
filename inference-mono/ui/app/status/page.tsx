"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleCheck, CircleSlash, TriangleAlert } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type {
  ModelServiceStatus,
  StatusComponent,
  StatusHistoryDay,
  StatusIncident,
  StatusPageResponse
} from "@/lib/types";

// HYP-58: per-model status page (see status.openrouter.ai for the pattern):
// a live snapshot plus a 30-day uptime bar per model. History is sampled by
// the Next.js server (lib/server/status-history.ts) since the inference-api
// only exposes the current state; latency is not shown for the same reason.
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

async function fetchStatus(): Promise<StatusPageResponse> {
  const res = await fetch("/api/status", { cache: "no-store" });
  if (!res.ok) throw new Error("Could not load status right now.");
  return (await res.json()) as StatusPageResponse;
}

export default function StatusPage() {
  const statusQuery = useQuery({
    queryKey: ["service-status"],
    queryFn: fetchStatus,
    refetchInterval: 30_000
  });
  const data = statusQuery.data;
  const ongoing = data?.incidents.filter((i) => i.resolved_at === null) ?? [];

  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main className="page-shell max-w-4xl py-12">
        <h1 className="text-3xl font-semibold tracking-normal sm:text-4xl">Talaris Status</h1>
        <p className="mt-3 text-muted-foreground">
          Live health of every Talaris component, probed every minute, with up to 90 days of
          uptime history.
        </p>

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

            {ongoing.length > 0 ? (
              <div className="mt-4 space-y-2">
                {ongoing.map((incident) => (
                  <IncidentRow key={incident.id} incident={incident} />
                ))}
              </div>
            ) : null}

            {data.groups.map((group) => (
              <section key={group.name} className="mt-10">
                <h2 className="font-display text-lg font-medium tracking-display">
                  {group.name}
                </h2>
                <div className="mt-3 space-y-2">
                  {group.components.map((component) => (
                    <ComponentRow
                      key={component.id}
                      component={component}
                      days={data.history_days}
                    />
                  ))}
                </div>
              </section>
            ))}

            <section className="mt-10">
              <h2 className="font-display text-lg font-medium tracking-display">
                Incident history
              </h2>
              <div className="mt-3 space-y-2">
                {data.incidents.length === 0 ? (
                  <Card>
                    <CardContent className="py-6 text-center text-sm text-muted-foreground">
                      No incidents recorded in the last 90 days.
                    </CardContent>
                  </Card>
                ) : (
                  data.incidents.map((incident) => (
                    <IncidentRow key={incident.id} incident={incident} />
                  ))
                )}
              </div>
            </section>

            <p className="mt-6 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Activity className="h-3.5 w-3.5" strokeWidth={1.5} />
              Last checked {formatDateTime(data.generated_at)}. Refreshes every 30 seconds.
              History begins when monitoring started; earlier days show as no data.
            </p>
          </>
        ) : null}
      </main>
      <SiteFooter />
    </div>
  );
}

function formatDuration(startIso: string, endIso: string | null) {
  const ms = (endIso ? new Date(endIso).getTime() : Date.now()) - new Date(startIso).getTime();
  const minutes = Math.max(1, Math.round(ms / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} h ${minutes % 60} min` : `${Math.floor(hours / 24)} d`;
}

function IncidentRow({ incident }: { incident: StatusIncident }) {
  const ongoing = incident.resolved_at === null;
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
        <div className="flex items-center gap-3">
          <StatusIcon status={incident.status} className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <div className="text-sm">
              {incident.component_name}: {incident.status === "down" ? "outage" : "degraded performance"}
            </div>
            <div className="text-xs text-muted-foreground">
              {formatDateTime(incident.started_at)}, {ongoing ? "ongoing for " : "lasted "}
              {formatDuration(incident.started_at, incident.resolved_at)}
            </div>
          </div>
        </div>
        <Badge variant={ongoing ? STATUS_COPY[incident.status].badge : "outline"}>
          {ongoing ? "Ongoing" : "Resolved"}
        </Badge>
      </CardContent>
    </Card>
  );
}

function UptimeBar({ history, uptime }: { history: StatusHistoryDay[]; uptime: number | null }) {
  return (
    <div className="w-full">
      <div className="flex h-8 items-stretch gap-[2px]" role="img" aria-label="Daily uptime history">
        {history.map((d) => (
          <div
            key={d.day}
            title={
              d.status === null
                ? `${d.day}: no data`
                : `${d.day}: ${STATUS_COPY[d.status].label}, ${((d.uptime ?? 0) * 100).toFixed(1)}% up`
            }
            // Weight ladder, not traffic lights: solid = operational,
            // half-tone = degraded, hollow outline = down, faint = no data.
            className={
              d.status === "operational"
                ? "flex-1 rounded-[1px] bg-foreground"
                : d.status === "degraded"
                  ? "flex-1 rounded-[1px] bg-foreground/50"
                  : d.status === "down"
                    ? "flex-1 rounded-[1px] border border-foreground"
                    : "flex-1 rounded-[1px] bg-border"
            }
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>{history.length} days ago</span>
        <span>{uptime === null ? "No data yet" : `${(uptime * 100).toFixed(2)}% uptime`}</span>
        <span>Today</span>
      </div>
    </div>
  );
}

function ComponentRow({ component, days }: { component: StatusComponent; days: number }) {
  const copy = STATUS_COPY[component.status];
  return (
    <Card>
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <StatusIcon status={component.status} className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <div className="font-mono text-sm">{component.name}</div>
              {component.description || component.latency_ms !== null ? (
                <div className="text-xs text-muted-foreground">
                  {[component.description, component.latency_ms !== null ? `avg ${component.latency_ms} ms` : null]
                    .filter(Boolean)
                    .join(", ")}
                </div>
              ) : null}
            </div>
          </div>
          <Badge variant={copy.badge}>{copy.label}</Badge>
        </div>
        <UptimeBar history={component.history} uptime={component.uptime} />
      </CardContent>
    </Card>
  );
}
