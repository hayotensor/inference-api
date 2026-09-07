import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Talaris meter. A thin Graphite track with an Off-White fill and a 2px
 * radius -- the kit rules out fully-rounded shapes, and there is no color to
 * signal "over quota", so a meter that is at or past its limit steps its
 * track up to Stone Gray rather than turning red.
 */
export function Progress({
  value,
  className
}: {
  value: number;
  className?: string;
}) {
  const clamped = Math.min(100, Math.max(0, value));
  const atLimit = clamped >= 100;

  return (
    <div
      className={cn(
        "h-1.5 w-full overflow-hidden rounded-sm border transition-colors duration-base ease-standard",
        atLimit ? "border-border-strong bg-graphite" : "border-transparent bg-graphite",
        className
      )}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
    >
      <div
        className="h-full bg-foreground transition-[width] duration-slow ease-standard"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
