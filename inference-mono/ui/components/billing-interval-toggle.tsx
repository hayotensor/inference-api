"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { BillingInterval } from "@/lib/types";

interface BillingIntervalToggleProps {
  value: BillingInterval;
  onChange: (interval: BillingInterval) => void;
  /** Shown next to "Yearly" when annual plans are cheaper than 12x monthly. */
  savingsPercent?: number | null;
  className?: string;
}

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
      className={cn("inline-flex items-center gap-1 rounded-lg border bg-muted/40 p-1", className)}
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
              "focus-ring inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              active
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {option.label}
            {option.interval === "year" && savingsPercent ? (
              <Badge variant="success">Save {savingsPercent}%</Badge>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
