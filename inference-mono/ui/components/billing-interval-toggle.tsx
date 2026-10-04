"use client";

import { cn } from "@/lib/utils";
import type { BillingInterval } from "@/lib/types";

interface BillingIntervalToggleProps {
  value: BillingInterval;
  onChange: (interval: BillingInterval) => void;
  /** Shown next to "Yearly" when annual plans are cheaper than 12x monthly. */
  savingsPercent?: number | null;
  className?: string;
}

/**
 * Segmented control, Talaris-style: a 2px-radius Graphite frame with the
 * selected segment carrying the Eggshell fill. No shadow marks the selection
 * (the kit forbids them) -- the fill swap does all the work, and the savings
 * figure rides along in mono as system metadata.
 */
export function BillingIntervalToggle({
  value,
  onChange,
  savingsPercent,
  className
}: BillingIntervalToggleProps) {
  const options: { interval: BillingInterval; label: string }[] = [
    { interval: "month", label: "Monthly" },
    { interval: "year", label: "Yearly" }
  ];

  return (
    <div
      role="group"
      aria-label="Billing interval"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-sm border border-border p-0.5",
        className
      )}
    >
      {options.map((option) => {
        const active = value === option.interval;
        return (
          <button
            key={option.interval}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.interval)}
            className={cn(
              "focus-ring inline-flex items-center gap-2 rounded-sm px-4 py-1.5 text-[11px] font-medium uppercase tracking-label transition-colors duration-fast ease-standard",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-elevated hover:text-foreground"
            )}
          >
            {option.label}
            {option.interval === "year" && savingsPercent ? (
              <span
                className={cn(
                  "font-mono text-[10px] tracking-normal",
                  active ? "text-primary-foreground/70" : "text-stone"
                )}
              >
                −{savingsPercent}%
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
