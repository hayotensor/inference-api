import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";
import { statusStyles } from "@/lib/status";

/**
 * Talaris tag/badge -- 11px Inter Medium, UPPERCASE, 0.14em tracking, 2px
 * radius, 1px border.
 *
 * Semantic variants use the shared status palette. Use StatusBadge for domain
 * statuses so pages share the same status-to-color mapping. Default, outline,
 * and secondary remain neutral for metadata such as roles and model names.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-[11px] font-medium uppercase leading-4 tracking-label transition-colors duration-fast ease-standard [&_svg]:h-3 [&_svg]:w-3",
  {
    variants: {
      variant: {
        default: "border-border bg-elevated text-foreground",
        secondary: statusStyles.secondary.badge,
        outline: "border-border bg-transparent text-foreground",
        warning: statusStyles.warning.badge,
        destructive: statusStyles.destructive.badge,
        success: statusStyles.success.badge,
        info: statusStyles.info.badge
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
