import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Talaris card: Onyx fill, 1px Graphite border, 4px radius, generous padding.
 * No shadow -- elevation is tone, not blur.
 *
 * `interactive` opts into the kit's hover behaviour for cards that are also
 * links or buttons: fill Onyx -> Charcoal, border Graphite -> Stone Gray.
 * Never scale, never lift. Static cards do not react to the pointer at all.
 */
const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean }
>(({ className, interactive, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "rounded-lg border border-border bg-card text-card-foreground",
      interactive &&
        "transition-colors duration-base ease-standard hover:border-border-strong hover:bg-elevated",
      className
    )}
    {...props}
  />
));
Card.displayName = "Card";

const CardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("space-y-1.5 p-6", className)} {...props} />
));
CardHeader.displayName = "CardHeader";

const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn(
      "font-display text-base font-semibold leading-tight tracking-display",
      className
    )}
    {...props}
  />
));
CardTitle.displayName = "CardTitle";

const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn("text-sm leading-relaxed text-muted-foreground", className)}
    {...props}
  />
));
CardDescription.displayName = "CardDescription";

const CardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
));
CardContent.displayName = "CardContent";

const CardFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex items-center p-6 pt-0", className)}
    {...props}
  />
));
CardFooter.displayName = "CardFooter";

/**
 * The kit's card eyebrow row: an UPPERCASE tracked label on the left, an
 * optional index counter ("01 / 03") or status on the right, over a thin rule.
 */
const CardEyebrow = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { index?: React.ReactNode }
>(({ className, children, index, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "flex items-center justify-between gap-4 border-b border-border px-6 py-3",
      className
    )}
    {...props}
  >
    <span className="eyebrow truncate">{children}</span>
    {index ? <span className="metadata text-xs shrink-0">{index}</span> : null}
  </div>
));
CardEyebrow.displayName = "CardEyebrow";

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardDescription,
  CardContent,
  CardEyebrow
};
