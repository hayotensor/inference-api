"use client";

import Link from "next/link";
import { ArrowRight, LayoutDashboard, MessageSquare, Moon, Sun } from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { buildChatPromptUrl } from "@/lib/chat-prompt";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

/**
 * Dark/light toggle for the public (signed-out) pages. Mirrors the one in
 * `DashboardShell` -- icon-only, shows the mode a click switches *to*.
 *
 * Exported (HYP-60) so `AuthFormShell` can render the same control on
 * login/signup/forgot-password/reset-password/verify-email, which have no
 * `PublicNav` of their own.
 */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const switchingToLight = theme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={switchingToLight ? "Switch to light mode" : "Switch to dark mode"}
      title={switchingToLight ? "Switch to light mode" : "Switch to dark mode"}
    >
      {switchingToLight ? <Sun strokeWidth={1.5} /> : <Moon strokeWidth={1.5} />}
    </Button>
  );
}

export function PublicNav() {
  const { isAuthenticated } = useAuth();

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur-[8px]">
      <div className="page-shell flex h-16 items-center justify-between">
        <AppLogo />
        {/* <nav className="hidden items-center gap-8 sm:flex">
          <Link
            href="/pricing"
            className="focus-ring text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Pricing
          </Link>
        </nav> */}
        <div className="flex items-center gap-2">
          {isAuthenticated ? (
            <>
              <Button variant="outline" size="sm" asChild>
                <Link href="/dashboard">
                  <LayoutDashboard strokeWidth={1.5} />
                  Dashboard
                </Link>
              </Button>
              {UI_DEMO_MODE ? (
                <Button size="sm" disabled title="Chat is unavailable in the local demo">
                  Chat
                  <MessageSquare strokeWidth={1.5} />
                </Button>
              ) : (
                <Button size="sm" asChild>
                  <a href={buildChatPromptUrl("")}>
                    Chat
                    <MessageSquare strokeWidth={1.5} />
                  </a>
                </Button>
              )}
              <ThemeToggle />
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
              <ThemeToggle />
            </>
          )}
        </div>
      </div>
    </header>
  );
}
