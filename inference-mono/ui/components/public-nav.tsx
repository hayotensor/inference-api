"use client";

import Link from "next/link";
import { ArrowRight, LayoutDashboard, LogOut } from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

export function PublicNav() {
  const { isAuthenticated, logout } = useAuth();

  return (
    <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur">
      <div className="page-shell flex h-16 items-center justify-between">
        <AppLogo />
        <nav className="hidden items-center gap-6 text-sm text-muted-foreground sm:flex">
          <Link href="/pricing" className="hover:text-foreground">
            Pricing
          </Link>
          <Link href="/phone-login" className="hover:text-foreground">
            Phone login
          </Link>
        </nav>
        <div className="flex items-center gap-2">
          {isAuthenticated ? (
            <>
              <Button variant="outline" size="sm" asChild>
                <Link href="/dashboard">
                  <LayoutDashboard />
                  Dashboard
                </Link>
              </Button>
              <Button variant="ghost" size="icon" onClick={() => void logout()}>
                <LogOut />
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
                  <ArrowRight />
                </Link>
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
