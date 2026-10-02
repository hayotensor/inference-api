"use client";

import * as React from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  CreditCard,
  ExternalLink,
  Gift,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MessagesSquare,
  Moon,
  Settings,
  Sun,
  WalletCards
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { AttestationBadge } from "@/components/attestation-badge";
import { LogoutConfirmDialog } from "@/components/logout-confirm-dialog";
import { PageLoadingState } from "@/components/loading-spinner";
import { SiteFooter } from "@/components/site-footer";
import { Button } from "@/components/ui/button";
import { AuthGate, useAuth } from "@/lib/auth";
import { navPathMatches, usePendingNavPath } from "@/lib/pending-nav";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * Dark/light toggle. Icon-only and unlabeled by the kit's nav conventions --
 * it sits with account chrome (log out), not with the section nav -- and
 * shows the mode a click switches *to* (a moon while on light, a sun while on
 * dark), which is the common convention for this control.
 */
function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme();
  const switchingToLight = theme === "dark";
  return (
    <Button
      variant="outline"
      size="icon"
      className={className}
      onClick={toggleTheme}
      aria-label={switchingToLight ? "Switch to light mode" : "Switch to dark mode"}
      title={switchingToLight ? "Switch to light mode" : "Switch to dark mode"}
    >
      {switchingToLight ? (
        <Sun strokeWidth={1.5} />
      ) : (
        <Moon strokeWidth={1.5} />
      )}
    </Button>
  );
}

/**
 * Launches chat in a new tab. Chat needs the httpOnly SSO cookie minted first
 * (see `ensureSsoCookie`), which is asynchronous -- but a `window.open()` that
 * happens *after* an `await` is no longer "in direct response to" the click,
 * so popup blockers kill it. The fix: open the tab synchronously, in the same
 * event-handler tick as the click (satisfying the popup blocker), pointed at
 * a blank page, then redirect that already-open tab once the cookie call
 * resolves. Tradeoff: this keeps `window.opener` set on the new tab (we can't
 * pass `noopener` and still hold a reference to redirect it), so chat -- a
 * first-party app we control -- can technically reach back into this window;
 * that's an acceptable surface for an app we own, but would not be for a
 * third-party destination.
 */
function ChatButton({
  ensureSsoCookie,
  className
}: {
  ensureSsoCookie: () => Promise<void>;
  className?: string;
}) {
  const openChat = React.useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      event.preventDefault();
      const chatWindow = window.open("about:blank", "_blank");
      void ensureSsoCookie()
        .catch(() => {
          // Best-effort: still navigate, the front door will redirect if needed.
        })
        .then(() => {
          if (chatWindow) chatWindow.location.href = CHAT_URL;
        });
    },
    [ensureSsoCookie]
  );

  return (
    <Button
      size="sm"
      // HYP-77: the one high-contrast action in the navbar. Off-white fill with
      // Void Black text in dark mode (brand ramp, no new colour); light mode
      // keeps the standard primary fill, since off-white would vanish there.
      className={cn(
        "border-transparent bg-[hsl(var(--off-white))] text-[hsl(var(--void-black))] hover:bg-[hsl(var(--off-white)/0.85)] [.light_&]:bg-primary [.light_&]:text-primary-foreground [.light_&]:hover:bg-primary-hover",
        className
      )}
      asChild
    >
      {/* href/target/rel are progressive enhancement (middle-click, "open link in
          new tab", copy link) -- the primary path always goes through onClick so
          the SSO cookie is guaranteed to be set before the tab is redirected. */}
      <a href={CHAT_URL} target="_blank" rel="noopener noreferrer" onClick={openChat}>
        <MessagesSquare strokeWidth={1.5} />
        Chat
        <ExternalLink strokeWidth={1.5} />
      </a>
    </Button>
  );
}

type NavItem = { href: string; label: string; icon: LucideIcon; external?: boolean };

// Chat is the rebranded Open-WebUI, never a Next.js route -- it is launched from
// the ChatButton below (always a new tab) rather than the sidebar nav. Path-based
// /chat needs Open-WebUI built with PUBLIC_BASE_PATH=/chat (its assets and SPA
// routes are otherwise root-absolute); with a stock build, point
// NEXT_PUBLIC_CHAT_URL at its own hostname instead.
const CHAT_URL = process.env.NEXT_PUBLIC_CHAT_URL || "/chat";

const navItems: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard/wallet", label: "Wallet", icon: WalletCards },
  { href: "/dashboard/api-keys", label: "API keys", icon: KeyRound },
  { href: "/dashboard/usage", label: "Usage", icon: BarChart3 },
  { href: "/dashboard/billing", label: "Billing", icon: CreditCard },
  { href: "/dashboard/referrals", label: "Referrals", icon: Gift },
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
      : "text-muted-foreground hover:bg-card hover:text-foreground"
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
  const [logoutConfirmOpen, setLogoutConfirmOpen] = React.useState(false);

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
      <div className="min-h-dvh bg-background">
        {/* One step of tone off the page (Onyx-on-Void in dark, white-on-Off-White
            in light), with a single hairline doing the partitioning -- `bg-card`
            so this tracks the active theme instead of being pinned dark. */}
        <aside className="pointer-events-auto fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-border bg-card lg:block">
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
              <div className="mt-3 flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1 justify-start"
                  onClick={() => setLogoutConfirmOpen(true)}
                >
                  <LogOut strokeWidth={1.5} />
                  Log out
                </Button>
                <ThemeToggle />
              </div>
            </div>
          </div>
        </aside>

        <div className="lg:pl-64">
          {/* Desktop-only top bar: the sidebar carries logo/account chrome, so this
              strip exists solely to put Chat top-right, per HYP-52. */}
          <div className="pointer-events-auto sticky top-0 z-20 hidden items-center justify-end border-b border-border bg-background/85 px-6 py-3 backdrop-blur-[8px] lg:flex">
            <ChatButton ensureSsoCookie={ensureSsoCookie} />
          </div>
          <header className="pointer-events-auto sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur-[8px] lg:hidden">
            <div className="flex h-16 items-center justify-between gap-2 px-4">
              <AppLogo />
              <div className="flex items-center gap-2">
                <ChatButton ensureSsoCookie={ensureSsoCookie} />
                <ThemeToggle />
                <Button variant="outline" size="sm" onClick={() => setLogoutConfirmOpen(true)}>
                  <LogOut strokeWidth={1.5} />
                  Log out
                </Button>
              </div>
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
          <SiteFooter />
        </div>
      </div>
      <LogoutConfirmDialog
        open={logoutConfirmOpen}
        onOpenChange={setLogoutConfirmOpen}
        onConfirm={() => {
          setLogoutConfirmOpen(false);
          void logout();
        }}
      />
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
