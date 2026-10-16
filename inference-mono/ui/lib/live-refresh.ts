"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

export const DASHBOARD_REFRESH_INTERVAL_MS = 60_000;

// Query-key roots for account data that changes while the dashboard is open
// (spend, balance, subscription state, key last-used times). Catalogues like
// plans, bundles and available models are left to their normal staleTime.
const LIVE_QUERY_ROOTS = new Set([
  "usage-current",
  "usage-allowance",
  "usage-hourly",
  "usage-by-model",
  "billing-subscription",
  "billing-history",
  "wallet-balance",
  "wallet-evm",
  "referrals-me"
]);

function isLiveQuery(queryKey: readonly unknown[]) {
  const [root, sub] = queryKey;
  if (root === "api-keys") return sub === undefined;
  return typeof root === "string" && LIVE_QUERY_ROOTS.has(root);
}

/**
 * Refetches the dashboard's live account data every minute. Only queries with
 * a mounted observer refetch; the rest are just marked stale. A hidden tab
 * skips ticks and catches up as soon as it becomes visible again, so a
 * backgrounded dashboard is never more than one tick out of date on return.
 */
export function useDashboardLiveRefresh(intervalMs = DASHBOARD_REFRESH_INTERVAL_MS) {
  const queryClient = useQueryClient();

  React.useEffect(() => {
    let lastRefresh = Date.now();

    const refresh = () => {
      lastRefresh = Date.now();
      void queryClient.invalidateQueries({
        predicate: (query) => isLiveQuery(query.queryKey)
      });
    };

    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, intervalMs);

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible" && Date.now() - lastRefresh >= intervalMs) {
        refresh();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [queryClient, intervalMs]);
}
