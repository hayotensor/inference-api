"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  ClipboardList,
  CreditCard,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Settings,
  Shield,
  Users
} from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { ErrorState, LoadingState } from "@/components/data-state";
import { PageLoadingState } from "@/components/loading-spinner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { adminApi, ApiError } from "@/lib/api";
import { AuthGate, useAuth } from "@/lib/auth";
import { navPathMatches, usePendingNavPath } from "@/lib/pending-nav";
import type { AdminMe, AdminPermission } from "@/lib/types";
import { cn } from "@/lib/utils";

const AdminContext = React.createContext<AdminMe | undefined>(undefined);
const ADMIN_IDENTITY_STALE_TIME_MS = 5 * 60_000;

const navItems: {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  permissions?: AdminPermission[];
}[] = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard, permissions: ["stats.read"] },
  { href: "/admin/users", label: "Users", icon: Users, permissions: ["users.read"] },
  { href: "/admin/api-keys", label: "API keys", icon: KeyRound, permissions: ["api_keys.read"] },
  {
    href: "/admin/billing/plans",
    label: "Plans",
    icon: CreditCard,
    permissions: ["billing.plans.read"]
  },
  { href: "/admin/audit-logs", label: "Audit logs", icon: ClipboardList, permissions: ["audit.read"] },
  { href: "/admin/settings", label: "Settings", icon: Settings }
];

function hasAny(admin: AdminMe, permissions?: AdminPermission[]) {
  if (!permissions || permissions.length === 0) return true;
  return permissions.some((permission) => admin.permissions.includes(permission));
}

function AdminAccessDenied() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-6">
      <div className="w-full max-w-md rounded-lg border bg-background p-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-destructive/10 text-destructive">
          <Shield className="h-6 w-6" />
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-normal">Admin access required</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your account is authenticated, but it does not have an active admin role.
        </p>
        <Button asChild className="mt-5">
          <Link href="/dashboard">Return to dashboard</Link>
        </Button>
      </div>
    </div>
  );
}

function AdminGate({ children }: { children: React.ReactNode }) {
  const adminQuery = useQuery({
    queryKey: ["admin", "me"],
    queryFn: adminApi.me,
    retry: false,
    staleTime: ADMIN_IDENTITY_STALE_TIME_MS
  });

  if (adminQuery.isLoading) return <LoadingState />;

  if (adminQuery.isError) {
    if (adminQuery.error instanceof ApiError && adminQuery.error.status === 403) {
      return <AdminAccessDenied />;
    }
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted/30 p-6">
        <ErrorState error={adminQuery.error} onRetry={() => void adminQuery.refetch()} />
      </div>
    );
  }

  if (!adminQuery.data) return null;

  return (
    <AdminContext.Provider value={adminQuery.data}>
      {children}
    </AdminContext.Provider>
  );
}

export function useAdmin() {
  const admin = React.useContext(AdminContext);
  if (!admin) throw new Error("useAdmin must be used inside AdminShell");
  return admin;
}

export function useAdminPermission(permission: AdminPermission) {
  const admin = useAdmin();
  return admin.permissions.includes(permission);
}

export function AdminShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate requireAuth>
      <AdminGate>
        <AdminFrame>{children}</AdminFrame>
      </AdminGate>
    </AuthGate>
  );
}

function AdminFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { activePath, isPending, markPendingHref } = usePendingNavPath(pathname);
  const admin = useAdmin();
  const { logout } = useAuth();
  const visibleNavItems = navItems.filter((item) => hasAny(admin, item.permissions));

  return (
    <div className="min-h-screen bg-muted/30">
      <aside className="pointer-events-auto fixed inset-y-0 left-0 z-30 hidden w-72 border-r bg-background lg:block">
        <div className="flex h-full flex-col">
          <div className="flex h-16 items-center justify-between px-5">
            <AppLogo />
            <Badge variant="outline">Admin</Badge>
          </div>
          <nav className="flex-1 space-y-1 px-3 py-4">
            {visibleNavItems.map((item) => {
              const Icon = item.icon;
              const active = navPathMatches(activePath, item.href, "/admin");
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
              <p className="truncate text-sm font-medium">{admin.email}</p>
              <p className="truncate text-xs text-muted-foreground">
                {admin.roles.map((role) => role.name).join(", ")}
              </p>
            </div>
            <div className="mt-3 grid gap-2">
              <Button asChild variant="outline" className="justify-start">
                <Link href="/dashboard">
                  <BarChart3 />
                  User dashboard
                </Link>
              </Button>
              <Button variant="outline" className="justify-start" onClick={() => void logout()}>
                <LogOut />
                Log out
              </Button>
            </div>
          </div>
        </div>
      </aside>

      <div className="lg:pl-72">
        <header className="pointer-events-auto sticky top-0 z-20 border-b bg-background/95 backdrop-blur lg:hidden">
          <div className="flex h-16 items-center justify-between px-4">
            <div className="flex items-center gap-2">
              <AppLogo />
              <Badge variant="outline">Admin</Badge>
            </div>
            <Button variant="outline" size="sm" onClick={() => void logout()}>
              <LogOut />
              Log out
            </Button>
          </div>
          <nav className="pointer-events-auto flex gap-2 overflow-x-auto border-t px-4 py-2">
            {visibleNavItems.map((item) => {
              const Icon = item.icon;
              const active = navPathMatches(activePath, item.href, "/admin");
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
  );
}
