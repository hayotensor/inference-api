import type { BillingInterval, BillingPlan } from "@/lib/types";

/**
 * Publishable key only. Checkout and customer-portal sessions are created by the
 * FastAPI backend (it owns the secret key); the UI just redirects to the returned
 * URL. This key is here so the UI can tell a test-mode account from a live one.
 */
export const stripePublishableKey =
  process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY ?? "";

export const isStripeTestMode = stripePublishableKey.startsWith("pk_test_");

export const isStripeConfigured = stripePublishableKey.length > 0;

export function planPriceCents(plan: BillingPlan, interval: BillingInterval) {
  return interval === "year" ? plan.annual_price_cents : plan.price_cents;
}

export function planPriceId(plan: BillingPlan, interval: BillingInterval) {
  return interval === "year" ? plan.stripe_annual_price_id : plan.stripe_price_id;
}

/** A plan is purchasable on an interval only if Stripe has a price for it. */
export function planIsPurchasable(plan: BillingPlan, interval: BillingInterval) {
  return Boolean(planPriceId(plan, interval));
}

/**
 * Free plans have no Stripe price on any interval and cost nothing. A paid tier
 * whose Stripe prices are not configured yet (e.g. Unlimited without
 * STRIPE_UNLIMITED_PRICE_ID) must not be shown as a "$0 / Start free" plan.
 */
export function planIsFree(plan: BillingPlan) {
  return (
    !plan.stripe_price_id &&
    !plan.stripe_annual_price_id &&
    !plan.price_cents &&
    !plan.is_unlimited
  );
}

/** Monthly-equivalent cost of an annual plan, for "billed yearly" copy. */
export function monthlyEquivalentCents(plan: BillingPlan) {
  return plan.annual_price_cents ? Math.round(plan.annual_price_cents / 12) : null;
}

/** Discount an annual price gives over 12x the monthly price, as a percentage. */
export function annualSavingsPercent(plan: BillingPlan) {
  if (!plan.price_cents || !plan.annual_price_cents) return null;
  const twelveMonths = plan.price_cents * 12;
  if (plan.annual_price_cents >= twelveMonths) return null;
  return Math.round(((twelveMonths - plan.annual_price_cents) / twelveMonths) * 100);
}
