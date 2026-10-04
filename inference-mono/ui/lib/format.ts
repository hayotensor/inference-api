export function formatNumber(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "0";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("en-US").format(numeric);
}

export function formatCompact(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "0";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1
  }).format(numeric);
}

export function formatMoney(
  cents: number | null | undefined,
  currency: string | null | undefined = "usd"
) {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return "\u2014";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: (currency || "usd").toUpperCase(),
    // Whole-dollar prices read better without ".00"; keep cents when they matter.
    minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
    maximumFractionDigits: 2
  }).format(cents / 100);
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(date);
}

export function formatRatePerMillion(
  cents: number | null | undefined,
  tokens: number | null | undefined,
  currency: string | null | undefined = "usd",
  unit: string = "tokens"
) {
  if (!cents || !tokens) return null;
  const perMillionCents = (cents / tokens) * 1_000_000;
  return `${formatMoney(Math.round(perMillionCents), currency)} / million ${unit}`;
}

// Renders a model's credit multiplier (e.g. the Decimal "2.0000" from the
// wire) as a short "x2" / "x0.6" / "x10.5" factor -- trailing zeros dropped,
// capped at 2 decimal places so odd Decimal precision doesn't leak into the
// UI.
export function formatMultiplier(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";
  const rounded = Math.round(numeric * 100) / 100;
  const text = Number.isInteger(rounded)
    ? String(rounded)
    : rounded.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return `×${text}`;
}

export function formatPercent(used: number, total: number) {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((used / total) * 100)));
}

export function shortenAddress(address: string | null | undefined) {
  if (!address) return "Not connected";
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}
