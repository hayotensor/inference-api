"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import type { AttestationStatusResponse } from "@/lib/types";

const RECHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

async function fetchAttestation(): Promise<AttestationStatusResponse> {
  try {
    const response = await fetch("/api/attestation-status", { cache: "no-store" });
    if (!response.ok) throw new Error("Attestation check failed");
    return await response.json() as AttestationStatusResponse;
  } catch {
    // A failed check must replace an earlier verified result.
    return { status: "unverified", checkedAt: new Date().toISOString(), reason: "error" };
  }
}

function useAttestationQuery() {
  return useQuery({
    queryKey: ["attestation-status"],
    // Let an in-flight check finish across Strict Mode's effect remount so
    // the remounted observer shares it instead of issuing another request.
    queryFn: fetchAttestation,
    staleTime: RECHECK_INTERVAL_MS,
    refetchInterval: RECHECK_INTERVAL_MS,
    refetchOnWindowFocus: true,
    retry: false
  });
}

const AttestationContext = React.createContext<ReturnType<typeof useAttestationQuery> | undefined>(undefined);

// One observer and polling timer for both responsive badges.
export function AttestationProvider({ children }: { children: React.ReactNode }) {
  const query = useAttestationQuery();
  return <AttestationContext.Provider value={query}>{children}</AttestationContext.Provider>;
}

export function useAttestation() {
  const query = React.useContext(AttestationContext);
  if (!query) throw new Error("useAttestation must be used inside AttestationProvider");
  return query;
}
