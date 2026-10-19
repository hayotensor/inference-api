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
    // Keep these in step with /privacy (HYP-80/81). Deliberately NOT "E2EE"
    // (the API decrypts to route; encryption is hop-by-hop) and NOT
    // "per-request attestation" (hardware attestation is ~hourly; each request
    // gets a TLS pin check + signed receipt). "Zero data retention" covers the
    // API path only: the chat app can store history if the user opts in.
    adds: [
      "OpenAI-compatible API",
      "Zero data retention: the API never stores prompts or outputs",
      "Every request served by an attested TEE, with a signed receipt",
      "Encrypted in transit, TLS-pinned to the enclave",
      "Community support"
    ]
  },
  starter: { inherits: "Free", adds: ["Usage dashboard", "Standard support"] },
  pro: { inherits: "Starter", adds: ["Priority support"] },
  business: { inherits: "Pro", adds: ["Team-scale monthly credit allowance"] },
  // The credits line already reads "Unlimited credits"; don't repeat it.
  unlimited: { inherits: "Business", adds: [] }
};

export function planTierCopy(plan: BillingPlan): TierCopy | null {
  return PLAN_TIER_COPY[plan.slug ?? plan.name.toLowerCase()] ?? null;
}
