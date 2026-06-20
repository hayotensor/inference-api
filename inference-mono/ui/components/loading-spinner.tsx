import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

type LoadingSpinnerProps = {
  className?: string;
  iconClassName?: string;
};

export function LoadingSpinner({
  className,
  iconClassName
}: LoadingSpinnerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("flex items-center justify-center", className)}
    >
      <Loader2
        aria-hidden="true"
        className={cn("h-5 w-5 animate-spin text-primary", iconClassName)}
      />
    </div>
  );
}

export function PageLoadingState({
  className
}: {
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-[50vh] items-center justify-center", className)}>
      <LoadingSpinner iconClassName="h-6 w-6" />
    </div>
  );
}
