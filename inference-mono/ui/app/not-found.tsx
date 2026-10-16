import Link from "next/link";

import { NotFoundGame } from "@/components/not-found-game";
import { Button } from "@/components/ui/button";

export const metadata = { title: "Page not found" };

// HYP-85: 404 with a playable Pac-Man-style maze. This file stays a server
// component (for metadata); the game itself is a client component that binds
// no keys until the visitor presses Play.
export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-background px-4 py-10 text-center">
      <div>
        <p className="eyebrow mb-2">Error 404</p>
        <h1 className="font-display text-[32px] font-medium leading-10 tracking-display">
          Page not found
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Whatever was here got eaten.
        </p>
      </div>
      <NotFoundGame />
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button asChild>
          <Link href="/">Back home</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/dashboard">Go to dashboard</Link>
        </Button>
      </div>
    </main>
  );
}
