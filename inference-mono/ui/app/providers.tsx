"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";

import { AuthProvider } from "@/lib/auth";

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
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
      <Toaster richColors closeButton position="top-right" />
    </QueryClientProvider>
  );
}
