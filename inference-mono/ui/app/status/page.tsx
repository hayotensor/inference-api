"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleCheck, CircleSlash, TriangleAlert } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { getStatusVariant, statusStyles } from "@/lib/status";
import { cn } from "@/lib/utils";
import type {
  ModelServiceStatus,
  StatusComponent,
  StatusHistoryDay,
  StatusIncident,
  StatusPageResponse
} from "@/lib/types";
import styles from "./status.module.css";

// HYP-58: per-model status page (see status.openrouter.ai for the pattern):
// a live snapshot plus a 90-day uptime bar per component (hover a day for
// its date, status, and uptime). History is sampled by
// the Next.js server (lib/server/status-history.ts) since the inference-api
// only exposes the current state; latency is not shown for the same reason.
//
// Shared status accents plus distinct icon shapes and text labels keep service
// health consistent with the dashboard without relying on color alone.

const STATUS_LABELS: Record<ModelServiceStatus, string> = {
  operational: "Operational",
  degraded: "Degraded",
  down: "Down"
};

function StatusIcon({ status, className }: { status: ModelServiceStatus; className?: string }) {
  const classes = cn(statusStyles[getStatusVariant(status)].text, className);
  if (status === "operational") return <CircleCheck className={classes} strokeWidth={1.5} />;
  if (status === "degraded") return <TriangleAlert className={classes} strokeWidth={1.5} />;
  return <CircleSlash className={classes} strokeWidth={1.5} />;
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
    <div className="flex min-h-dvh flex-col bg-background">
      <PublicNav />
      <main className="page-shell w-full flex-1 max-w-4xl py-12">
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
                  <StatusIcon status={data.overall_status} className="h-5 w-5" />
                  <span className="font-display text-lg font-medium tracking-display">
                    {OVERALL_COPY[data.overall_status]}
                  </span>
                </div>
                <StatusBadge status={data.overall_status}>{STATUS_LABELS[data.overall_status]}</StatusBadge>
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
          <StatusIcon status={incident.status} className="h-4 w-4 shrink-0" />
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
        <StatusBadge status={ongoing ? incident.status : "resolved"}>
          {ongoing ? "Ongoing" : "Resolved"}
        </StatusBadge>
      </CardContent>
    </Card>
  );
}

// Uptime bar colors (HYP-58 feedback): green = operational, yellow = degraded,
// red = down, neutral = no samples that day. The hues live in
// status.module.css so they stay scoped to the bars.
const BAR_CLASS: Record<ModelServiceStatus | "none", string> = {
  operational: styles.barOperational,
  degraded: styles.barDegraded,
  down: styles.barDown,
  none: styles.barNone
};

// Phones show the most recent 30 days so each bar stays wide enough to tap.
const MOBILE_DAYS = 30;

function formatDay(day: string) {
  // `day` is a UTC calendar date; format it in UTC so it never shifts a day.
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric"
  });
}

function formatUptime(uptime: number) {
  const pct = uptime * 100;
  return `${pct === 100 || pct === 0 ? pct.toFixed(0) : pct.toFixed(2)}% uptime`;
}

function UptimeBar({ history, uptime }: { history: StatusHistoryDay[]; uptime: number | null }) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [active, setActive] = React.useState<{ index: number; left: number } | null>(null);
  const mobileStart = Math.max(0, history.length - MOBILE_DAYS);

  const show = (index: number, target: HTMLElement) => {
    const container = containerRef.current;
    if (!container) return;
    const box = container.getBoundingClientRect();
    const bar = target.getBoundingClientRect();
    setActive({ index, left: bar.left - box.left + bar.width / 2 });
  };

  const day = active ? history[active.index] : null;
  const width = containerRef.current?.clientWidth ?? 0;
  // Keep the 12rem tooltip inside the card instead of overflowing at the ends.
  const tooltipLeft = active ? Math.min(Math.max(active.left, 96), Math.max(96, width - 96)) : 0;

  return (
    <div>
      <div
        ref={containerRef}
        className="relative"
        onMouseLeave={() => setActive(null)}
        role="img"
        aria-label={`Daily uptime history: ${uptime === null ? "no data yet" : formatUptime(uptime)}`}
      >
        <div className="flex h-8 gap-[2px]" aria-hidden="true">
          {history.map((d, index) => (
            <div
              key={d.day}
              className={cn(
                "h-full min-w-0 flex-1 rounded-[1px] transition-opacity",
                BAR_CLASS[d.status ?? "none"],
                index < mobileStart && "hidden sm:block",
                active && active.index !== index && "opacity-60"
              )}
              onMouseEnter={(event) => show(index, event.currentTarget)}
              onPointerDown={(event) => show(index, event.currentTarget)}
            />
          ))}
        </div>
        {day ? (
          <div
            className="pointer-events-none absolute bottom-full z-20 mb-2 w-48 -translate-x-1/2 rounded-md border border-border bg-popover px-3 py-2 text-popover-foreground shadow-lg"
            style={{ left: tooltipLeft }}
            role="tooltip"
          >
            <div className="text-xs font-medium">{formatDay(day.day)}</div>
            <div className="mt-1 flex items-center gap-1.5 text-xs">
              <span className={cn("h-2 w-2 shrink-0 rounded-full", BAR_CLASS[day.status ?? "none"])} />
              <span>{day.status === null ? "No data" : STATUS_LABELS[day.status]}</span>
              {day.uptime !== null ? (
                <span className="ml-auto text-muted-foreground">{formatUptime(day.uptime)}</span>
              ) : null}
            </div>
            {day.status === null ? (
              <div className="mt-1 text-[11px] text-muted-foreground">Not monitored on this day.</div>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>
          <span className="sm:hidden">{Math.min(history.length, MOBILE_DAYS)} days ago</span>
          <span className="hidden sm:inline">{history.length} days ago</span>
        </span>
        <span>{uptime === null ? "No data yet" : formatUptime(uptime)}</span>
        <span>Today</span>
      </div>
    </div>
  );
}

function ComponentRow({ component, days }: { component: StatusComponent; days: number }) {
  return (
    <Card>
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <StatusIcon status={component.status} className="h-4 w-4 shrink-0" />
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
          <StatusBadge status={component.status}>{STATUS_LABELS[component.status]}</StatusBadge>
        </div>
        <UptimeBar history={component.history} uptime={component.uptime} />
      </CardContent>
    </Card>
  );
}
