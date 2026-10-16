"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";

import { AuthProvider } from "@/lib/auth";
import { DemoBanner } from "@/components/demo-banner";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { WalletProvider } from "@/lib/wallet";

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
      <QueryClientProvider client={queryClient}>
        <WalletProvider>
          <AuthProvider>{children}</AuthProvider>
          <DemoBanner />
          <ThemedToaster />
        </WalletProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

// Split out so it can read the theme via context -- `sonner`'s own `theme`
// prop just toggles its built-in palette, which we override anyway, but it
// also flips aria/data attributes some of its internals key off of.
function ThemedToaster() {
  const { theme } = useTheme();
  return (
    // Keep neutral toast panels and tint their icons with the shared accents.
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
          success: "[&_[data-icon]]:!text-success",
          warning: "[&_[data-icon]]:!text-warning",
          error: "[&_[data-icon]]:!text-destructive",
          info: "[&_[data-icon]]:!text-info",
          actionButton: "!rounded-sm !bg-primary !text-primary-foreground",
          cancelButton: "!rounded-sm !bg-transparent !text-muted-foreground",
          closeButton: "!rounded-sm !border-border !bg-elevated !text-muted-foreground"
        }
      }}
    />
  );
}
