"use client";

import Link from "next/link";
import * as NavigationMenu from "@radix-ui/react-navigation-menu";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Boxes,
  ChevronDown,
  Code2,
  FileText,
  HelpCircle,
  LayoutDashboard,
  LifeBuoy,
  LockKeyhole,
  MessageSquare,
  Moon,
  Palette,
  ShieldCheck,
  Sun,
  type LucideIcon
} from "lucide-react";

import { AppLogo } from "@/components/app-logo";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { buildChatPromptUrl } from "@/lib/chat-prompt";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

type NavLink = {
  label: string;
  href: string;
  icon: LucideIcon;
  external?: boolean;
};

const DOCS_URL = (process.env.NEXT_PUBLIC_DOCS_URL || "https://talaris-inference-docs.rizzo.network").replace(/\/+$/, "");

const productLinks: NavLink[] = [
  { label: "API", href: "/docs", icon: Code2 },
  { label: "Models", href: "/models", icon: Boxes },
  { label: "Chat", href: "https://chat.talaris.xyz", icon: MessageSquare, external: true }
];

const resourceLinks: NavLink[] = [
  { label: "API documentation", href: `${DOCS_URL}/api-reference/`, icon: Code2, external: true },
  { label: "Miner documentation", href: `${DOCS_URL}/miner-node/`, icon: BookOpen, external: true },
  { label: "Privacy policy", href: "/privacy", icon: LockKeyhole },
  { label: "Terms of Service", href: "/terms", icon: FileText },
  { label: "Privacy & Security", href: "/security", icon: ShieldCheck },
  { label: "Brand kit", href: "/brand", icon: Palette },
  { label: "Customer service", href: "mailto:contact@talaris.xyz", icon: LifeBuoy, external: true },
  { label: "Status", href: "/status", icon: Activity },
  { label: "FAQ", href: "/faq", icon: HelpCircle },
  { label: "Blog", href: "/blog", icon: BookOpen }
];

const navTriggerClassName =
  "group focus-ring inline-flex h-10 items-center justify-center gap-1.5 rounded-lg px-2 text-[13px] font-medium tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:bg-elevated hover:text-foreground focus-visible:bg-elevated focus-visible:text-foreground data-[state=open]:bg-elevated data-[state=open]:text-foreground motion-reduce:transition-none sm:px-3";

function PublicNavDropdown({ label, links }: { label: string; links: NavLink[] }) {
  return (
    <NavigationMenu.Item>
      <NavigationMenu.Trigger className={navTriggerClassName}>
        {label}
        <ChevronDown
          aria-hidden="true"
          className="h-3.5 w-3.5 transition-transform duration-base ease-standard group-data-[state=open]:rotate-180 motion-reduce:transition-none"
          strokeWidth={1.5}
        />
      </NavigationMenu.Trigger>
      <NavigationMenu.Content className="left-0 top-0 w-[min(22rem,calc(100vw-2rem))] motion-safe:data-[motion^=from-]:animate-in data-[motion^=from-]:fade-in-0 data-[motion^=to-]:absolute motion-safe:data-[motion^=to-]:animate-out data-[motion^=to-]:fade-out-0 duration-base ease-standard motion-reduce:transition-none">
        <ul className={`grid max-h-[calc(100dvh-8rem)] gap-1 overflow-y-auto p-2 ${links.length > 3 ? "sm:grid-cols-2" : ""}`}>
          {links.map(({ label: linkLabel, href, icon: Icon, external }) => {
            const className =
              "focus-ring flex items-center gap-3 rounded-lg px-3 py-3 text-[13px] font-medium text-muted-foreground transition-colors duration-fast ease-standard hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground motion-reduce:transition-none";
            const content = (
              <>
                <Icon aria-hidden="true" className="h-4 w-4 shrink-0" strokeWidth={1.5} />
                <span>{linkLabel}</span>
                {href.startsWith("https://") ? (
                  <ArrowUpRight aria-hidden="true" className="ml-auto h-3.5 w-3.5" strokeWidth={1.5} />
                ) : null}
              </>
            );

            return (
              <li key={linkLabel}>
                <NavigationMenu.Link asChild>
                  {external ? (
                    <a href={href} className={className}>{content}</a>
                  ) : (
                    <Link href={href} className={className}>{content}</Link>
                  )}
                </NavigationMenu.Link>
              </li>
            );
          })}
        </ul>
      </NavigationMenu.Content>
    </NavigationMenu.Item>
  );
}

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
      <div className="page-shell flex min-h-16 flex-wrap items-center justify-between gap-x-4">
        <AppLogo className="shrink-0 [&>[data-wordmark]]:hidden sm:[&>[data-wordmark]]:inline" />
        <NavigationMenu.Root
          aria-label="Main navigation"
          delayDuration={150}
          className="relative order-last flex w-full justify-center pb-3 lg:order-none lg:w-auto lg:pb-0"
        >
          <NavigationMenu.List className="flex items-center gap-1">
            <PublicNavDropdown label="Products" links={productLinks} />
            <NavigationMenu.Item>
              <NavigationMenu.Link asChild>
                <Link href="/pricing" className={navTriggerClassName}>
                  Pricing
                </Link>
              </NavigationMenu.Link>
            </NavigationMenu.Item>
            <PublicNavDropdown label="Resources" links={resourceLinks} />
          </NavigationMenu.List>
          <div className="absolute left-0 top-full flex w-full justify-center pt-2">
            <NavigationMenu.Viewport className="relative h-[var(--radix-navigation-menu-viewport-height)] w-[var(--radix-navigation-menu-viewport-width)] shrink-0 overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground transition-[width,height] duration-base ease-standard motion-safe:data-[state=open]:animate-in motion-safe:data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:slide-in-from-top-1 data-[state=closed]:slide-out-to-top-1 motion-reduce:transition-none" />
          </div>
        </NavigationMenu.Root>
        <div className="flex items-center gap-2 py-3">
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
