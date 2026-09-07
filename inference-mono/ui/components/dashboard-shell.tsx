"use client";

import * as React from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  CreditCard,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MessagesSquare,
  MessageSquareText,
  Settings
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { AttestationBadge } from "@/components/attestation-badge";
import { PageLoadingState } from "@/components/loading-spinner";
import { Button } from "@/components/ui/button";
import { AuthGate, useAuth } from "@/lib/auth";
import { navPathMatches, usePendingNavPath } from "@/lib/pending-nav";
import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: LucideIcon; external?: boolean };

// Chat is the rebranded Open-WebUI, never a Next.js route, so it is always a
// full-page navigation. Path-based /chat needs Open-WebUI built with
// PUBLIC_BASE_PATH=/chat (its assets and SPA routes are otherwise root-absolute);
// with a stock build, point NEXT_PUBLIC_CHAT_URL at its own hostname instead.
const CHAT_URL = process.env.NEXT_PUBLIC_CHAT_URL || "/chat";

const navItems: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: CHAT_URL, label: "Chat", icon: MessagesSquare, external: true },
  { href: "/dashboard/api-keys", label: "API keys", icon: KeyRound },
  { href: "/dashboard/usage", label: "Usage", icon: BarChart3 },
  { href: "/dashboard/billing", label: "Billing", icon: CreditCard },
  { href: "/dashboard/playground", label: "Playground", icon: MessageSquareText },
  { href: "/dashboard/settings", label: "Settings", icon: Settings }
];

// Nav is system metadata, so it takes the kit's label treatment: UPPERCASE
// Inter Medium at 0.14em. The active item is marked by an Off-White left rule
// and a Charcoal fill rather than a colored pill -- the system has no accent
// color to make a pill out of.
const navItemClass = (active: boolean) =>
  cn(
    "pointer-events-auto flex h-10 items-center gap-3 border-l-2 border-transparent px-4 text-[11px] font-medium uppercase tracking-label transition-colors duration-fast ease-standard",
    active
      ? "border-l-foreground bg-elevated text-foreground"
      : "text-muted-foreground hover:bg-onyx hover:text-foreground"
  );

const navItemClassMobile = (active: boolean) =>
  cn(
    "pointer-events-auto flex h-9 shrink-0 items-center gap-2 rounded-sm border px-3 text-[11px] font-medium uppercase tracking-label transition-colors duration-fast ease-standard",
    active
      ? "border-border bg-elevated text-foreground"
      : "border-transparent text-muted-foreground hover:text-foreground"
  );

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { activePath, isPending, markPendingHref } = usePendingNavPath(pathname);
  const { user, logout, ensureSsoCookie } = useAuth();

  // Chat lives behind a front door that authenticates by the httpOnly SSO cookie.
  // Navigating there before that cookie is in place gets the user bounced to the
  // login screen, so mint it first and then leave.
  const leaveForExternal = React.useCallback(
    async (href: string, event: React.MouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      try {
        await ensureSsoCookie();
      } catch {
        // Best-effort: still navigate, the front door will redirect if needed.
      }
      window.location.assign(href);
    },
    [ensureSsoCookie]
  );

  const renderNav = (mobile: boolean) =>
    navItems.map((item) => {
      const Icon = item.icon;
      const active = navPathMatches(activePath, item.href, "/dashboard");
      const className = mobile ? navItemClassMobile(active) : navItemClass(active);
      const inner = (
        <>
          <Icon className="h-4 w-4 shrink-0" strokeWidth={1.5} />
          {item.label}
        </>
      );
      if (item.external) {
        return (
          <a
            key={item.href}
            href={item.href}
            className={className}
            onClick={(event) => leaveForExternal(item.href, event)}
          >
            {inner}
          </a>
        );
      }
      return (
        <Link
          key={item.href}
          href={item.href}
          onClick={(event) => markPendingHref(item.href, event)}
          className={className}
        >
          {inner}
        </Link>
      );
    });

  return (
    <AuthGate requireAuth>
      <div className="min-h-screen bg-background">
        {/* Onyx rail against the Void Black page: hierarchy from tone, with a
            single Graphite hairline doing the partitioning. */}
        <aside className="pointer-events-auto fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-border bg-onyx lg:block">
          <div className="flex h-full flex-col">
            <div className="flex h-16 items-center border-b border-border px-5">
              <AppLogo />
            </div>
            <nav className="flex-1 space-y-0.5 py-4">{renderNav(false)}</nav>
            <div className="border-t border-border p-4">
              <AttestationBadge />
              <div className="mt-4 min-w-0 border-t border-border pt-4">
                <p className="truncate text-sm font-medium text-foreground">
                  {user?.full_name || user?.email}
                </p>
                <p className="metadata mt-0.5 truncate text-xs">{user?.email}</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="mt-3 w-full justify-start"
                onClick={() => void logout()}
              >
                <LogOut strokeWidth={1.5} />
                Log out
              </Button>
            </div>
          </div>
        </aside>

        <div className="lg:pl-64">
          <header className="pointer-events-auto sticky top-0 z-20 border-b border-border bg-void/85 backdrop-blur-[8px] lg:hidden">
            <div className="flex h-16 items-center justify-between px-4">
              <AppLogo />
              <Button variant="outline" size="sm" onClick={() => void logout()}>
                <LogOut strokeWidth={1.5} />
                Log out
              </Button>
            </div>
            <div className="border-t border-border px-4 py-2">
              <AttestationBadge />
            </div>
            <nav className="pointer-events-auto flex gap-1 overflow-x-auto border-t border-border px-4 py-2">
              {renderNav(true)}
            </nav>
          </header>
          <main className="page-shell py-8 lg:py-12">
            {isPending ? <PageLoadingState /> : children}
          </main>
        </div>
      </div>
    </AuthGate>
  );
}

/**
 * Page masthead. One headline per surface (the kit rules out stacked H1s),
 * strongly left-aligned, over an eyebrow that names the section and a thin
 * rule that separates it from the content below.
 */
export function DashboardPageHeader({
  title,
  description,
  eyebrow,
  action
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-8 border-b border-border pb-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          {eyebrow ? <p className="eyebrow mb-2">{eyebrow}</p> : null}
          <h1 className="font-display text-[32px] font-medium leading-10 tracking-display">
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}
