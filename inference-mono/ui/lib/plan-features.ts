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
    // Keep these in step with /privacy (HYP-80/81/97). E2EE is claimed only for
    // API requests made through the talaris-e2ee client (the plain API and the
    // chat app are still hop-by-hop: the API decrypts to route). NOT
    // "per-request attestation" (hardware attestation is ~hourly; each request
    // gets a TLS pin check + signed receipt). "Zero data retention" covers the
    // API path only: the chat app can store history if the user opts in.
    adds: [
      "OpenAI-compatible API",
      "Zero data retention: the API never stores prompts or outputs",
      "Every request served by an attested TEE, with a signed receipt",
      "Optional end-to-end encrypted API requests (talaris-e2ee client)",
      "Otherwise encrypted in transit, TLS-pinned to the enclave",
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
