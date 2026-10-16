import type {
  AdminAuditLog, AdminBillingPlan, AdminRole, AdminUserDetail, ApiKeyRead,
  BillingHistoryEvent, BillingSubscription, CatalogModel, EvmWallet, ModelCatalogResponse, ReferralSummary, StatusPageResponse,
  UsageEvent, UsagePeriod, User, WalletTopupBundle
} from "@/lib/types";
import { DEMO_EMAIL } from "@/lib/demo-mode";

const now = new Date();
export const timestamp = now.toISOString();
const daysAgo = (days: number) => new Date(now.getTime() - days * 86_400_000).toISOString();
const periodStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString();
const periodEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)).toISOString();
export const demoUserId = "00000000-0000-4000-8000-000000000001";
export const modelNames = ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen3-32B"];

export const ownerRole: AdminRole = {
  id: "demo-owner", slug: "owner", name: "Owner", description: "Local demo owner", system: true,
  permissions: [
    "admins.read", "admins.write", "admins.owner.write", "users.read", "users.write",
    "users.disable", "users.sessions.revoke", "users.sensitive.read", "api_keys.read",
    "api_keys.write", "api_keys.revoke", "billing.read", "billing.sensitive.read",
    "billing.plans.read", "billing.plans.write", "billing.subscriptions.write", "usage.read",
    "usage.write", "wallets.read", "wallets.write", "wallets.sensitive.read", "audit.read",
    "audit.sensitive.read", "stats.read"
  ]
};
export const supportRole: AdminRole = {
  id: "demo-support", slug: "support", name: "Support", description: "Sample support role",
  system: true, permissions: ["users.read", "api_keys.read", "usage.read", "stats.read"]
};

const user: User = {
  id: demoUserId, email: DEMO_EMAIL, full_name: "Local Developer", is_active: true,
  is_superuser: false, is_verified: true, phone_number: null, phone_verified_at: null,
  created_at: daysAgo(60), oauth_providers: [], has_verifiable_email: true
};

const keys: ApiKeyRead[] = ["Dashboard development", "Sample integration"].map((name, index) => ({
  id: `demo-key-${index + 1}`, user_id: user.id, name, environment: "test",
  prefix: "sk_test_demo", last_four: `000${index + 1}`,
  scopes: ["models:read", "inference:write", "usage:read"], allowed_models: index ? [modelNames[0]] : null,
  rate_limit_per_minute: 120, created_at: daysAgo(20 - index), expires_at: null,
  last_used_at: daysAgo(index), revoked_at: null
}));

const plans: AdminBillingPlan[] = [
  { name: "Free", price: 0, allowance: 100_000 },
  { name: "Starter", price: 1900, allowance: 2_000_000 },
  { name: "Pro", price: 4900, allowance: 10_000_000 }
].map(({ name, price, allowance }, index) => ({
  id: `demo-plan-${name.toLowerCase()}`, slug: name.toLowerCase(), name,
  description: `${name} sample plan`, stripe_price_id: price ? `price_demo_${index}` : null,
  stripe_annual_price_id: price ? `price_demo_annual_${index}` : null,
  price_cents: price, annual_price_cents: price ? price * 10 : null, currency: "usd",
  monthly_token_allowance: allowance, is_unlimited: false, active: true, visible: true,
  sort_order: index, features: { api_access: true }, created_at: daysAgo(90), updated_at: timestamp
}));

const subscription: BillingSubscription = {
  id: "demo-subscription", stripe_subscription_id: "sub_demo", stripe_price_id: plans[1].stripe_price_id,
  plan_id: plans[1].id, plan_name: plans[1].name, interval: "month", amount_cents: plans[1].price_cents,
  currency: "usd", status: "active", current_period_start: periodStart, current_period_end: periodEnd,
  cancel_at_period_end: false
};

const events: UsageEvent[] = Array.from({ length: 24 }, (_, index) => {
  const prompt = 450 + index * 51;
  const completion = 180 + index * 23;
  const createdAt = new Date(Math.max(new Date(periodStart).getTime(), now.getTime() - index * 3_600_000)).toISOString();
  return {
    id: `demo-event-${index}`, user_id: user.id, api_key_id: keys[index % 2].id,
    request_id: `demo-request-${index}`, model: modelNames[index % 2], prompt_tokens: prompt,
    completion_tokens: completion, raw_total_tokens: prompt + completion, model_multiplier: "1",
    charged_tokens: prompt + completion, status: "succeeded", created_at: createdAt, settled_at: createdAt
  };
});
const used = events.reduce((sum, event) => sum + event.charged_tokens, 0);
const usage: UsagePeriod = {
  id: "demo-period", user_id: user.id, period_start: periodStart, period_end: periodEnd,
  stripe_allowance: plans[1].monthly_token_allowance, ethereum_erc20_allowance: 0,
  substrate_native_allowance: 0, manual_allowance: 50_000, total_allowance: 2_050_000,
  used_tokens: used, remaining_tokens: 2_050_000 - used, calculated_at: timestamp
};
const wallet: EvmWallet = {
  address: "0x000000000000000000000000000000000000dEaD",
  verified_at: daysAgo(10), last_balance_sync_at: timestamp
};
const users: AdminUserDetail[] = [
  { ...user, api_key_count: keys.length, active_api_key_count: keys.length, updated_at: timestamp,
    roles: [ownerRole], billing_status: "active", wallet_address: wallet.address,
    current_period_remaining_tokens: usage.remaining_tokens },
  { ...user, id: "00000000-0000-4000-8000-000000000002", email: "alex@example.com",
    full_name: "Alex Morgan", api_key_count: 0, active_api_key_count: 0, updated_at: timestamp,
    roles: [], billing_status: "free", wallet_address: null, current_period_remaining_tokens: 100_000 },
  { ...user, id: "00000000-0000-4000-8000-000000000003", email: "sam@example.com",
    full_name: "Sam Rivera", is_active: false, is_verified: false, api_key_count: 0,
    active_api_key_count: 0, updated_at: timestamp, roles: [], billing_status: "free",
    wallet_address: null, current_period_remaining_tokens: 100_000 }
];
// HYP-91: sample `/billing/history`, newest first.
const historyRow = (
  index: number, days: number, event_type: string, description: string,
  extra: Partial<BillingHistoryEvent> = {}
): BillingHistoryEvent => ({
  id: `demo-billing-event-${index}`, event_type, description, plan_name: plans[1].name,
  amount_cents: null, currency: "usd", tokens: null, created_at: daysAgo(days), ...extra
});
const billingHistory: BillingHistoryEvent[] = [
  historyRow(1, 2, "wallet_topup", "Pay-as-you-go top-up: 2,600,000 credits",
    { plan_name: null, amount_cents: 2500, tokens: 2_600_000 }),
  historyRow(2, 5, "subscription_renewed", `Renewed ${plans[1].name} (monthly)`, { amount_cents: plans[1].price_cents }),
  historyRow(3, 18, "subscription_reactivated", `Scheduled cancellation of ${plans[1].name} (monthly) withdrawn`),
  historyRow(4, 20, "subscription_cancel_scheduled", `Cancellation scheduled for ${plans[1].name} (monthly)`),
  historyRow(5, 27, "wallet_topup", "Pay-as-you-go top-up: 1,000,000 credits",
    { plan_name: null, amount_cents: 1000, tokens: 1_000_000 }),
  historyRow(6, 35, "subscription_renewed", `Renewed ${plans[1].name} (monthly)`, { amount_cents: plans[1].price_cents }),
  historyRow(7, 50, "subscription_payment", `Payment for ${plans[1].name} (monthly)`, { amount_cents: 1200 }),
  historyRow(8, 52, "subscription_updated", `Changed plan from ${plans[2].name} (monthly) to ${plans[1].name} (monthly)`,
    { amount_cents: plans[1].price_cents }),
  historyRow(9, 58, "payment_failed", `Payment failed for ${plans[2].name} (monthly)`,
    { plan_name: plans[2].name, amount_cents: plans[2].price_cents }),
  historyRow(10, 60, "subscription_created", `Subscribed to ${plans[2].name} (monthly)`,
    { plan_name: plans[2].name, amount_cents: plans[2].price_cents })
];

const auditLogs: AdminAuditLog[] = ["auth.login", "api_key.create", "admin.owner.bootstrap"].map((action, index) => ({
  id: `demo-audit-${index}`, user_id: user.id, actor_user_id: user.id, action, actor: user.email,
  target_type: "user", target_id: user.id, result: "success", request_id: `demo-audit-request-${index}`,
  ip_address: "127.0.0.1", user_agent: "Local UI demo", metadata: { demo: true }, created_at: daysAgo(index)
}));

export const walletBundles: WalletTopupBundle[] = [
  { id: "topup_10", usd_cents: 1000, tokens: 1_000_000, currency: "usd" },
  { id: "topup_25", usd_cents: 2500, tokens: 2_600_000, currency: "usd" },
  { id: "topup_50", usd_cents: 5000, tokens: 5_400_000, currency: "usd" },
  { id: "topup_100", usd_cents: 10000, tokens: 11_200_000, currency: "usd" }
];
export const referrals: ReferralSummary = {
  referral_code: "LOCALDEMO", referral_link: "/r/LOCALDEMO",
  total_referrals: 3, total_tokens_earned: 30_000,
  recent_earnings: [1, 2, 3].map((index) => ({
    id: `demo-referral-${index}`, tokens_credited: 10_000, usd_equivalent_cents: 100, created_at: daysAgo(index)
  }))
};
export const statusPage: StatusPageResponse = {
  generated_at: timestamp, overall_status: "operational", history_days: 14, incidents: [],
  groups: [{ name: "Demo services", components: ["Main API", "Inference API", ...modelNames].map((name, index) => ({
    id: `demo-service-${index}`, name, description: "Sample status for local UI development",
    status: "operational", uptime: 1, latency_ms: 42 + index * 15,
    history: Array.from({ length: 14 }, (_, day) => ({
      day: daysAgo(13 - day).slice(0, 10), status: "operational", uptime: 1
    }))
  })) }]
};

// HYP-92: sample `/models` catalog. The two billed demo models come first so
// usage/API-key fixtures stay consistent; the rest exercise every category tab
// and availability state.
const catalogRow = (
  id: string, rest: Omit<CatalogModel, "id" | "name" | "label" | "healthy_miner_count" | "registered_miner_count">,
  healthy: number, registered: number
): CatalogModel => ({
  id, name: id.split("/").pop() ?? id, label: null, ...rest,
  healthy_miner_count: healthy, registered_miner_count: registered
});
export const modelCatalog: ModelCatalogResponse = {
  generated_at: timestamp,
  models: [
    catalogRow(modelNames[0], { provider: "Meta", family: "Llama", category: "chat", parameter_size: "70B", token_multiplier: "2.0000", status: "operational" }, 4, 4),
    catalogRow(modelNames[1], { provider: "Qwen", family: "Qwen", category: "chat", parameter_size: "32B", token_multiplier: "1.0000", status: "operational" }, 3, 3),
    catalogRow("meta-llama/Llama-3.1-8B-Instruct", { provider: "Meta", family: "Llama", category: "chat", parameter_size: "8B", token_multiplier: "0.5000", status: "operational" }, 6, 6),
    catalogRow("deepseek-ai/DeepSeek-R1-Distill-Llama-70B", { provider: "DeepSeek", family: "DeepSeek", category: "reasoning", parameter_size: "70B", token_multiplier: "2.5000", status: "operational" }, 2, 2),
    catalogRow("Qwen/Qwen2.5-Coder-32B-Instruct", { provider: "Qwen", family: "Qwen", category: "code", parameter_size: "32B", token_multiplier: "1.0000", status: "degraded" }, 0, 1),
    catalogRow("Qwen/Qwen2.5-VL-7B-Instruct", { provider: "Qwen", family: "Qwen", category: "vision", parameter_size: "7B", token_multiplier: "0.7500", status: "operational" }, 1, 2),
    catalogRow("BAAI/bge-m3", { provider: "BAAI", family: "BGE", category: "embedding", parameter_size: null, token_multiplier: "0.1000", status: "down" }, 0, 0)
  ]
};

// In-memory edits last until a full page reload. Never persist real-looking data
// alongside the backend's data or accept a server token as a demo credential.
export function createDemoState() {
  return structuredClone({
    user, keys, plans, subscription, events, usage, users, auditLogs, wallet: wallet as EvmWallet | null,
    referralCode: referrals.referral_code, billingHistory
  });
}
