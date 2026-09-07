"use client";

import Link from "next/link";
import { ArrowRight, LayoutDashboard, LogOut } from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

export function PublicNav() {
  const { isAuthenticated, logout } = useAuth();

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-void/85 backdrop-blur-[8px]">
      <div className="page-shell flex h-16 items-center justify-between">
        <AppLogo />
        <nav className="hidden items-center gap-8 sm:flex">
          <Link
            href="/pricing"
            className="focus-ring text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Pricing
          </Link>
        </nav>
        <div className="flex items-center gap-2">
          {isAuthenticated ? (
            <>
              <Button variant="outline" size="sm" asChild>
                <Link href="/dashboard">
                  <LayoutDashboard strokeWidth={1.5} />
                  Dashboard
                </Link>
              </Button>
              <Button variant="ghost" size="icon" onClick={() => void logout()}>
                <LogOut strokeWidth={1.5} />
                <span className="sr-only">Log out</span>
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/login">Log in</Link>
              </Button>
              <Button size="sm" asChild>
                <Link href="/signup">
                  Sign up
                  <ArrowRight strokeWidth={1.5} />
                </Link>
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
