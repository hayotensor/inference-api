/** Shared semantic accents for badges, status icons, and history indicators. */
export const statusStyles = {
  success: {
    badge: "border-success-border bg-success-background text-success",
    text: "text-success",
    indicator: "fill-success"
  },
  warning: {
    badge: "border-warning-border bg-warning-background text-warning",
    text: "text-warning",
    indicator: "fill-warning"
  },
  destructive: {
    badge: "border-destructive-border bg-destructive-background text-destructive",
    text: "text-destructive",
    indicator: "fill-none stroke-destructive"
  },
  info: {
    badge: "border-info-border bg-info-background text-info",
    text: "text-info",
    indicator: "fill-info"
  },
  secondary: {
    badge: "border-border bg-transparent text-muted-foreground",
    text: "text-muted-foreground",
    indicator: "fill-border"
  }
} as const;

export type StatusVariant = keyof typeof statusStyles;

// Green: successful or healthy. Amber: attention or an intentional stop.
// Red: failed or unavailable. Blue: informational or in progress.
// Neutral: no active state. Unknown API values also remain neutral.
const STATUS_VARIANTS = {
  active: "success",
  current: "success",
  enabled: "success",
  paid: "success",
  succeeded: "success",
  settled: "success",
  success: "success",
  verified: "success",
  connected: "success",
  linked: "success",
  operational: "success",
  resolved: "success",

  canceled: "warning",
  canceling: "warning",
  past_due: "warning",
  incomplete: "warning",
  paused: "warning",
  expired: "warning",
  revoked: "warning",
  inactive: "warning",
  disabled: "warning",
  unverified: "warning",
  stale: "warning",
  degraded: "warning",

  error: "destructive",
  failed: "destructive",
  failure: "destructive",
  denied: "destructive",
  unpaid: "destructive",
  incomplete_expired: "destructive",
  down: "destructive",

  trialing: "info",
  pending: "info",
  processing: "info",
  queued: "info",
  running: "info",
  checking: "info",
  reserved: "info",

  free: "secondary",
  released: "secondary",
  disconnected: "secondary",
  not_connected: "secondary",
  unknown: "secondary"
} as const satisfies Record<string, StatusVariant>;

export function getStatusVariant(status: string): StatusVariant {
  const key = status.trim().toLowerCase().replace(/[\s-]+/g, "_");
  return Object.prototype.hasOwnProperty.call(STATUS_VARIANTS, key)
    ? STATUS_VARIANTS[key as keyof typeof STATUS_VARIANTS]
    : "secondary";
}
