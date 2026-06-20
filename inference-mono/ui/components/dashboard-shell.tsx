"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  CreditCard,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MessageSquareText,
  Settings,
  WalletCards
} from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { PageLoadingState } from "@/components/loading-spinner";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { AuthGate, useAuth } from "@/lib/auth";
import { navPathMatches, usePendingNavPath } from "@/lib/pending-nav";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard/api-keys", label: "API keys", icon: KeyRound },
  { href: "/dashboard/usage", label: "Usage", icon: BarChart3 },
  { href: "/dashboard/billing", label: "Billing", icon: CreditCard },
  { href: "/dashboard/wallet", label: "Wallet", icon: WalletCards },
  { href: "/dashboard/playground", label: "Playground", icon: MessageSquareText },
  { href: "/dashboard/settings", label: "Settings", icon: Settings }
];

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { activePath, isPending, markPendingHref } = usePendingNavPath(pathname);
  const { user, logout } = useAuth();

  return (
    <AuthGate requireAuth>
      <div className="min-h-screen bg-muted/30">
        <aside className="pointer-events-auto fixed inset-y-0 left-0 z-30 hidden w-64 border-r bg-background lg:block">
          <div className="flex h-full flex-col">
            <div className="flex h-16 items-center px-5">
              <AppLogo />
            </div>
            <nav className="flex-1 space-y-1 px-3 py-4">
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = navPathMatches(activePath, item.href, "/dashboard");
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={(event) => markPendingHref(item.href, event)}
                    className={cn(
                      "pointer-events-auto flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground",
                      active && "bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground"
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
            <div className="p-4">
              <Separator className="mb-4" />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {user?.full_name || user?.email}
                </p>
                <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
              </div>
              <Button
                variant="outline"
                className="mt-3 w-full justify-start"
                onClick={() => void logout()}
              >
                <LogOut />
                Log out
              </Button>
            </div>
          </div>
        </aside>

        <div className="lg:pl-64">
          <header className="pointer-events-auto sticky top-0 z-20 border-b bg-background/95 backdrop-blur lg:hidden">
            <div className="flex h-16 items-center justify-between px-4">
              <AppLogo />
              <Button variant="outline" size="sm" onClick={() => void logout()}>
                <LogOut />
                Log out
              </Button>
            </div>
            <nav className="pointer-events-auto flex gap-2 overflow-x-auto border-t px-4 py-2">
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = navPathMatches(activePath, item.href, "/dashboard");
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={(event) => markPendingHref(item.href, event)}
                    className={cn(
                      "pointer-events-auto flex h-9 shrink-0 items-center gap-2 rounded-md px-3 text-sm text-muted-foreground",
                      active && "bg-primary text-primary-foreground"
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </header>
          <main className="page-shell py-6 lg:py-8">
            {isPending ? <PageLoadingState /> : children}
          </main>
        </div>
      </div>
    </AuthGate>
  );
}

export function DashboardPageHeader({
  title,
  description,
  action
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-normal">{title}</h1>
        {description ? (
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
