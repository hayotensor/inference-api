import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * The Talaris monolith mark.
 *
 * Inlined rather than loaded from `public/brand/talaris-icon-reversed.svg` so
 * it inherits `currentColor` and can never render at the wrong tone against
 * Onyx vs. Void Black. Geometry is the design system's asset, unchanged: a
 * tapered slab, a vertical seam, and a single horizontal course line.
 */
export function TalarisMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      aria-hidden="true"
      className={cn("h-7 w-7", className)}
    >
      <path d="M22 8 L42 14 L42 56 L22 56 Z" fill="currentColor" />
      {/* Seam + course line are cut out of the slab, so they take the ground
          tone rather than a second brand color. */}
      <path d="M32 11 L32 56" stroke="hsl(var(--background))" strokeWidth="1.25" />
      <path d="M22 22 L42 22" stroke="hsl(var(--background))" strokeWidth="1" />
    </svg>
  );
}

/**
 * Header lockup: mark + wordmark.
 *
 * The wordmark is live text in Sora rather than the kit's `<text>`-based SVG,
 * which would fall back to a system face whenever the browser renders it as an
 * image. Per the kit the lockup appears once per surface, in the header only.
 */
export function AppLogo({
  className,
  href = "/",
  showWordmark = true
}: {
  className?: string;
  href?: string;
  showWordmark?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "focus-ring inline-flex items-center gap-2.5 text-foreground transition-colors duration-fast hover:text-eggshell",
        className
      )}
      aria-label="Talaris"
    >
      <TalarisMark className="h-7 w-7 shrink-0" />
      {showWordmark ? (
        <span className="font-display text-[15px] font-medium uppercase leading-none tracking-[0.16em]">
          Talaris
        </span>
      ) : null}
    </Link>
  );
}
