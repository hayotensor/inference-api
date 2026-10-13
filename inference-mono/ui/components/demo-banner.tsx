"use client";

import Link from "next/link";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

export function DemoBanner() {
  if (!UI_DEMO_MODE) return null;
  return (
    <div className="fixed inset-x-3 bottom-3 z-50 mx-auto flex w-fit max-w-[calc(100%-1.5rem)] flex-wrap items-center justify-center gap-x-4 gap-y-2 rounded-lg border border-border-strong bg-elevated px-4 py-2 text-xs shadow-lg" aria-label="Local demo controls">
      <span>Local demo · Sample data</span>
      <Link className="underline" href="/dashboard">Dashboard</Link>
      <Link className="underline" href="/admin">Admin</Link>
      <button className="underline" onClick={() => window.location.reload()}>Reset demo data</button>
    </div>
  );
}
