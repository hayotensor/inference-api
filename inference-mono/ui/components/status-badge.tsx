import { Badge, type BadgeProps } from "@/components/ui/badge";
import { getStatusVariant } from "@/lib/status";

type StatusBadgeProps = Omit<BadgeProps, "variant"> & { status: string };

export function StatusBadge({ status, children, ...props }: StatusBadgeProps) {
  return (
    <Badge variant={getStatusVariant(status)} {...props}>
      {children ?? status.replace(/[_-]+/g, " ")}
    </Badge>
  );
}

// A scheduled cancellation still has an active/trialing subscription in Stripe.
// Keep that status visible alongside the amber renewal notice, including when
// a payment problem needs to be shown at the same time.
export function SubscriptionStatusBadge({
  status,
  cancelAtPeriodEnd
}: {
  status: string;
  cancelAtPeriodEnd: boolean;
}) {
  return (
    <div className="flex flex-wrap justify-end gap-1.5">
      <StatusBadge status={status} />
      {cancelAtPeriodEnd && status !== "canceled" ? (
        <StatusBadge status="canceling">Cancels at period end</StatusBadge>
      ) : null}
    </div>
  );
}
