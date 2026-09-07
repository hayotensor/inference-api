import { AlertCircle } from "lucide-react";

import { LoadingSpinner } from "@/components/loading-spinner";
import { Button } from "@/components/ui/button";
import { apiErrorMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

export function LoadingState({
  className
}: {
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-40 items-center justify-center", className)}>
      <LoadingSpinner />
    </div>
  );
}

export function ErrorState({
  error,
  onRetry
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-lg border border-border-strong bg-card p-8 text-center">
      <AlertCircle className="h-5 w-5 text-foreground" strokeWidth={1.5} />
      <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
        {apiErrorMessage(error)}
      </p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-card p-10 text-center">
      <h3 className="font-display text-base font-medium tracking-display">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}
