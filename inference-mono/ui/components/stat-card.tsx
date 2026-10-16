import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Talaris stat tile.
 *
 * The kit gives cards an eyebrow + index row over a thin rule, then the
 * content. Stats use neutral colors so semantic accents remain reserved for
 * statuses. The icon is a Warm Gray line glyph; the figure has the strongest
 * contrast, which is what makes it read first.
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
        // Eggshell is a fixed fill, not a themed one -- it needs Void Black
        // text in both dark and light mode, so this is `text-void`
        // (hardcoded), never `text-background` (which flips under `.light`).
        emphasis && "border-transparent bg-eggshell text-void",
        className
      )}
    >
      <div
        className={cn(
          "flex items-center justify-between gap-3 border-b px-5 py-3",
          emphasis ? "border-void/15" : "border-border"
        )}
      >
        <span
          className={cn(
            "eyebrow truncate",
            emphasis && "text-void/70"
          )}
        >
          {label}
        </span>
        <Icon
          className={cn(
            "h-4 w-4 shrink-0",
            emphasis ? "text-void/70" : "text-muted-foreground"
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
              emphasis && "text-void/70"
            )}
          >
            {detail}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
