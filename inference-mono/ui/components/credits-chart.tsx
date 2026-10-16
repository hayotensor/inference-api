"use client";

import * as React from "react";

import type { UsageHourlyResponse } from "@/lib/types";
import { formatNumber } from "@/lib/format";

type Point = { day: string; credits: number };

const dayKey = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

/** Regroup the API's UTC hour buckets into local calendar days, zero-filled across [start, end]. */
export function creditsByDay(buckets: UsageHourlyResponse["data"], start: Date, end: Date): Point[] {
  const totals = new Map<string, number>();
  for (const bucket of buckets) {
    const key = dayKey(new Date(bucket.hour));
    totals.set(key, (totals.get(key) ?? 0) + bucket.charged_tokens);
  }
  const points: Point[] = [];
  const cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const last = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  while (cursor <= last && points.length < 400) {
    const key = dayKey(cursor);
    points.push({ day: key, credits: totals.get(key) ?? 0 });
    cursor.setDate(cursor.getDate() + 1);
  }
  return points;
}

/** Dependency-free bar chart of credits used per day. */
export function CreditsChart({ points }: { points: Point[] }) {
  const [hover, setHover] = React.useState<number | null>(null);
  const width = 640;
  const height = 200;
  const pad = { top: 12, right: 8, bottom: 24, left: 8 };
  const max = Math.max(1, ...points.map((p) => p.credits));
  const slot = (width - pad.left - pad.right) / Math.max(1, points.length);
  const barW = Math.max(1, slot * 0.7);
  const active = hover !== null ? points[hover] : null;

  return (
    <div>
      <p className="metadata mb-2 h-4 text-xs">
        {active ? `${active.day}: ${formatNumber(active.credits)} credits` : "Hover a bar for daily credits"}
      </p>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="Credits used per day"
        onMouseLeave={() => setHover(null)}
      >
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={height - pad.bottom}
          y2={height - pad.bottom}
          className="stroke-border"
        />
        {points.map((p, i) => {
          const h = ((height - pad.top - pad.bottom) * p.credits) / max;
          const x = pad.left + i * slot + (slot - barW) / 2;
          return (
            <g key={p.day} onMouseEnter={() => setHover(i)}>
              <rect x={pad.left + i * slot} y={0} width={slot} height={height} fill="transparent" />
              <rect
                x={x}
                y={height - pad.bottom - h}
                width={barW}
                height={h}
                className={hover === i ? "fill-foreground" : "fill-muted-foreground"}
              />
            </g>
          );
        })}
        {points.length > 0 ? (
          <>
            <text x={pad.left} y={height - 6} className="fill-muted-foreground text-[10px]">
              {points[0].day}
            </text>
            <text
              x={width - pad.right}
              y={height - 6}
              textAnchor="end"
              className="fill-muted-foreground text-[10px]"
            >
              {points[points.length - 1].day}
            </text>
          </>
        ) : null}
      </svg>
    </div>
  );
}
