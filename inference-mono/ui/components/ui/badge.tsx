import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Talaris tag/badge -- 11px Inter Medium, UPPERCASE, 0.14em tracking, 2px
 * radius, 1px border.
 *
 * The kit has no status colors, so the variants form a *weight* ladder instead
 * of a hue one, from quietest to loudest:
 *
 *   secondary    transparent fill, Graphite border, Warm Gray text
 *   outline      transparent fill, Graphite border, Off-White text
 *   default      Charcoal fill, Graphite border
 *   warning      transparent fill, Stone Gray border  (border steps up)
 *   destructive  transparent fill, Off-White border   (border steps up again)
 *   success      solid Eggshell fill on Void Black    (full inversion)
 *
 * Reading order is deliberate: a filled tag is the affirmative terminal state
 * (verified, active, paid), and a heavier border means "needs attention".
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-[11px] font-medium uppercase leading-4 tracking-label transition-colors duration-fast ease-standard [&_svg]:h-3 [&_svg]:w-3",
  {
    variants: {
      variant: {
        default: "border-border bg-elevated text-foreground",
        secondary: "border-border bg-transparent text-muted-foreground",
        outline: "border-border bg-transparent text-foreground",
        warning: "border-border-strong bg-transparent text-foreground",
        destructive: "border-foreground bg-transparent text-foreground",
        // Eggshell is a fixed fill, not a themed one -- `text-void` (hardcoded)
        // keeps it "Eggshell fill on Void Black" in both dark and light mode,
        // where `text-background` would flip to Off-White and vanish.
        success: "border-transparent bg-eggshell text-void"
      }
    },
    defaultVariants: {
      variant: "default"
    }
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
