"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RainbowKitProvider } from "@rainbow-me/rainbowkit";
import { WagmiProvider } from "wagmi";
import { Toaster } from "sonner";

import { AuthProvider } from "@/lib/auth";
import { rainbowKitDarkTheme, rainbowKitLightTheme } from "@/lib/rainbowkit-theme";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { wagmiConfig } from "@/lib/wagmi";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: (failureCount, error) => {
              const status =
                error && typeof error === "object" && "status" in error
                  ? Number((error as { status?: number }).status)
                  : 0;
              if ([401, 403, 404, 429].includes(status)) return false;
              return failureCount < 2;
            }
          }
        }
      })
  );

  return (
    <ThemeProvider>
      {/* WagmiProvider outside QueryClientProvider (wagmi v2 uses tanstack query
          internally too) -- sharing the one QueryClient is the documented setup,
          not two competing caches. */}
      <WagmiProvider config={wagmiConfig}>
        <QueryClientProvider client={queryClient}>
          <ThemedRainbowKitProvider>
            <AuthProvider>{children}</AuthProvider>
            <ThemedToaster />
          </ThemedRainbowKitProvider>
        </QueryClientProvider>
      </WagmiProvider>
    </ThemeProvider>
  );
}

// Split out so it can read the theme via context and hand RainbowKit's modal
// the matching brand palette (see lib/rainbowkit-theme.ts) instead of its
// default blue.
function ThemedRainbowKitProvider({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  return (
    <RainbowKitProvider theme={theme === "dark" ? rainbowKitDarkTheme : rainbowKitLightTheme}>
      {children}
    </RainbowKitProvider>
  );
}

// Split out so it can read the theme via context -- `sonner`'s own `theme`
// prop just toggles its built-in palette, which we override anyway, but it
// also flips aria/data attributes some of its internals key off of.
function ThemedToaster() {
  const { theme } = useTheme();
  return (
    // `richColors` is deliberately off: it paints toasts green/red/amber, and
    // the Talaris system has no status hues. Toasts are Charcoal panels with a
    // Graphite hairline (or their light-mode counterparts), same as every
    // other elevated surface.
    <Toaster
      closeButton
      position="top-right"
      theme={theme}
      toastOptions={{
        classNames: {
          toast:
            "!rounded-lg !border !border-border !bg-elevated !text-foreground !font-sans !shadow-none",
          title: "!text-sm !font-medium",
          description: "!text-sm !text-muted-foreground",
          actionButton: "!rounded-sm !bg-primary !text-primary-foreground",
          cancelButton: "!rounded-sm !bg-transparent !text-muted-foreground",
          closeButton: "!rounded-sm !border-border !bg-elevated !text-muted-foreground"
        }
      }}
    />
  );
}
