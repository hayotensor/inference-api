import Link from "next/link";

import { cn } from "@/lib/utils";

/** Use the brand asset as a mask so the mark follows the theme's text color. */
export function TalarisMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block h-7 w-11 bg-current", className)}
      style={{
        mask: 'url("/brand/talaris-4-24.svg") center / contain no-repeat',
        WebkitMask: 'url("/brand/talaris-4-24.svg") center / contain no-repeat'
      }}
    />
  );
}

/** Shared site logo with a live-text wordmark in Sora. */
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
      <TalarisMark className="shrink-0" />
      {showWordmark ? (
        <span
          data-wordmark
          className="font-display text-[15px] font-medium uppercase leading-none tracking-[0.16em]"
        >
          Talaris
        </span>
      ) : null}
    </Link>
  );
}
