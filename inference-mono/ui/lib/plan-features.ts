import type { BillingPlan } from "@/lib/types";

/**
 * HYP-81: pricing-card copy is cumulative -- each tier reads "Everything in
 * <previous tier>" followed only by what that tier adds. Keyed by plan slug;
 * plans with an unknown slug fall back to the generic list in the page.
 * `inherits` names the plan whose card this one builds on.
 */
type TierCopy = { inherits: string | null; adds: string[] };

export const PLAN_TIER_COPY: Record<string, TierCopy> = {
  free: {
    inherits: null,
    adds: [
      "OpenAI-compatible API",
      "Encrypted in transit, pinned to attested enclaves",
      "Remote-attested model providers + signed receipts",
      "Prompts and outputs are never stored",
      "Community support"
    ]
  },
  starter: { inherits: "Free", adds: ["Usage dashboard", "Standard support"] },
  pro: { inherits: "Starter", adds: ["Priority support"] },
  business: { inherits: "Pro", adds: ["Team-scale monthly credit allowance"] },
  unlimited: { inherits: "Business", adds: ["No monthly credit cap"] }
};

export function planTierCopy(plan: BillingPlan): TierCopy | null {
  return PLAN_TIER_COPY[plan.slug ?? plan.name.toLowerCase()] ?? null;
}
