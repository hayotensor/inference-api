import Link from "next/link";

import { AppLogo } from "@/components/app-logo";

/**
 * Basic footer (HYP-62): TOS, privacy, contact -- the minimum a footer needs,
 * shown on the homepage and every dashboard page, signed in or out. Auth
 * state doesn't change anything here (unlike `PublicNav`'s CTAs, HYP-49) --
 * legal/contact links are the same for every visitor.
 *
 * Kept as one shared component rather than duplicated per-surface so the link
 * set only has one place to maintain. Uses `page-shell` for the same gutter
 * as the nav/header above it; `DashboardShell` renders this outside its
 * `lg:pl-64` content column is unnecessary since it's placed inside `<main>`
 * there, so it already inherits the sidebar offset correctly.
 */
export function SiteFooter() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border">
      <div className="page-shell flex flex-col gap-6 py-8 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
          <AppLogo showWordmark={false} className="shrink-0" />
          <p className="metadata text-xs">
            &copy; {year} Talaris. All rights reserved.
          </p>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Link
            href="/terms"
            className="focus-ring text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Terms of Service
          </Link>
          <Link
            href="/privacy"
            className="focus-ring text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Privacy Policy
          </Link>
          <Link
            href="/pricing"
            className="focus-ring text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Pricing
          </Link>
          <a
            href="mailto:contact@talaris.xyz"
            className="focus-ring text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Contact
          </a>
        </nav>
      </div>
    </footer>
  );
}
