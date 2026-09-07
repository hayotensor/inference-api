import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Talaris stat tile.
 *
 * The kit gives cards an eyebrow + index row over a thin rule, then the
 * content. There are no tone colors here by design -- the old blue/green/amber
 * icon chips are gone, because the system has no chromatic accents and no
 * status hues. The icon is a Warm Gray line glyph; the figure is the only
 * thing at full Off-White, which is what makes it read first.
 *
 * `emphasis` promotes a single tile in a row (the one figure that matters most
 * on the page) by inverting it to the Eggshell fill. Use it sparingly -- one
 * per row at most, or the emphasis stops meaning anything.
 */
export function StatCard({
  label,
  value,
  icon: Icon,
  detail,
  emphasis = false,
  className
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  detail?: string;
  emphasis?: boolean;
  className?: string;
}) {
  return (
    <Card
      className={cn(
        "flex flex-col",
        emphasis && "border-transparent bg-eggshell text-background",
        className
      )}
    >
      <div
        className={cn(
          "flex items-center justify-between gap-3 border-b px-5 py-3",
          emphasis ? "border-background/15" : "border-border"
        )}
      >
        <span
          className={cn(
            "eyebrow truncate",
            emphasis && "text-background/70"
          )}
        >
          {label}
        </span>
        <Icon
          className={cn(
            "h-4 w-4 shrink-0",
            emphasis ? "text-background/70" : "text-muted-foreground"
          )}
          strokeWidth={1.5}
        />
      </div>
      <div className="flex flex-1 flex-col justify-end px-5 pb-5 pt-4">
        <p className="numeral truncate text-[32px] font-medium leading-10">
          {value}
        </p>
        {detail ? (
          <p
            className={cn(
              "metadata mt-1.5 truncate text-xs",
              emphasis && "text-background/70"
            )}
          >
            {detail}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
