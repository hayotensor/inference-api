import Link from "next/link";

import { cn } from "@/lib/utils";

export function AppLogo({ className }: { className?: string }) {
  return (
    <Link
      href="/"
      className={cn("flex items-center gap-2 text-sm font-semibold", className)}
    >
      <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-sm font-bold text-primary-foreground">
        IA
      </span>
      <span>Inference API</span>
    </Link>
  );
}
