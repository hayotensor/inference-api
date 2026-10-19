import { DEMO_EMAIL, DEMO_PASSWORD, UI_DEMO_MODE } from "@/lib/demo-mode";
import type { ApiKeyRead, BillingInterval, TokenResponse, UsageByModel } from "@/lib/types";
import {
  createDemoState, demoUserId, modelCatalog, modelNames, ownerRole, referrals,
  supportRole, timestamp, walletBundles
} from "./fixtures";

export class DemoError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let state = createDemoState();
export function resetDemoState() {
  state = createDemoState();
}

function tokens(): TokenResponse {
  return {
    access_token: "local-demo-access", refresh_token: "local-demo-refresh", token_type: "bearer",
    expires_in: 86400, user: state.user
  };
}

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new DemoError(404, "Sample record not found.");
  return value;
}

function paginate<T extends object>(rows: T[], query: URLSearchParams) {
  const search = query.get("search")?.toLowerCase();
  const status = query.get("status");
  const filtered = rows.filter((row) => {
    if (search && !JSON.stringify(row).toLowerCase().includes(search)) return false;
    if (!status || status === "all") return true;
    const record = row as Record<string, unknown>;
    if (status === "active") return (record.is_active ?? record.active ?? !record.revoked_at) === true;
    if (status === "disabled") return record.is_active === false;
    if (status === "inactive") return record.active === false;
    if (status === "verified") return record.is_verified === true;
    if (status === "unverified") return record.is_verified === false;
    if (status === "revoked") return Boolean(record.revoked_at);
    if (status === "expired") return Boolean(record.expires_at && Date.parse(String(record.expires_at)) < Date.now());
    return record.result === status;
  });
  const sort = query.get("sort") ?? "-created_at";
  const field = sort.replace(/^-/, "") as keyof T;
  filtered.sort((a, b) => String(a[field] ?? "").localeCompare(String(b[field] ?? ""), undefined, { numeric: true }) * (sort.startsWith("-") ? -1 : 1));
  const limit = Math.max(1, Number(query.get("limit")) || 50);
  const offset = Math.max(0, Number(query.get("offset")) || 0);
  return { data: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset };
}

// HYP-91: demo plan changes/cancellations show up in the billing history table.
function recordBillingEvent(event_type: string, description: string, amount_cents: number | null = null) {
  state.billingHistory.unshift({
    id: `demo-billing-event-${Date.now()}-${state.billingHistory.length}`, event_type, description,
    plan_name: state.subscription.plan_name, amount_cents, currency: "usd", tokens: null,
    created_at: new Date().toISOString()
  });
}

function usageHourly() {
  const totals = new Map<string, number>();
  for (const event of state.events) {
    const hour = new Date(event.created_at);
    hour.setUTCMinutes(0, 0, 0);
    const key = hour.toISOString();
    totals.set(key, (totals.get(key) ?? 0) + event.charged_tokens);
  }
  return {
    period_start: state.usage.period_start,
    period_end: state.usage.period_end,
    data: [...totals].sort(([a], [b]) => a.localeCompare(b)).map(([hour, charged_tokens]) => ({ hour, charged_tokens }))
  };
}

function byModel(): UsageByModel[] {
  return modelNames.map((model) => {
    const events = state.events.filter((event) => event.model === model);
    return {
      model, request_count: events.length, last_used_at: events[0]?.created_at ?? null,
      prompt_tokens: events.reduce((sum, event) => sum + event.prompt_tokens, 0),
      completion_tokens: events.reduce((sum, event) => sum + event.completion_tokens, 0),
      charged_tokens: events.reduce((sum, event) => sum + event.charged_tokens, 0)
    };
  });
}

function recalculateUsage() {
  state.usage.total_allowance = state.usage.stripe_allowance + state.usage.manual_allowance;
  state.usage.remaining_tokens = Math.max(0, state.usage.total_allowance - state.usage.used_tokens);
  state.usage.calculated_at = new Date().toISOString();
  return state.usage;
}

function adminUsers() {
  return state.users.map((user) => user.id === demoUserId ? {
    ...user, ...state.user, api_key_count: state.keys.length,
    active_api_key_count: state.keys.filter((key) => !key.revoked_at).length,
    billing_status: state.subscription.status, wallet_address: state.wallet?.address ?? null,
    current_period_remaining_tokens: state.usage.remaining_tokens
  } : user);
}

// Codes the demo pretends belong to someone else, so the 409 path is testable.
const demoTakenReferralCodes = ["taken", "already-taken"];

function referralSummary() {
  const link = `/r/${state.referralCode}`;
  return {
    ...referrals,
    referral_code: state.referralCode,
    referral_link: typeof window === "undefined" ? link : new URL(link, window.location.origin).href
  };
}

function handle(path: string, method: string, payload?: unknown): unknown {
  const url = new URL(path, "http://demo.local");
  const route = url.pathname;
  const body = (payload ?? {}) as Record<string, unknown>;
  const key = `${method} ${route}`;

  switch (key) {
    case "POST /auth/login":
      if (body.email !== DEMO_EMAIL || body.password !== DEMO_PASSWORD) {
        throw new DemoError(401, "Use the Enter local demo button or dev@example.com / local-demo.");
      }
      return tokens();
    case "POST /auth/refresh": return tokens();
    case "POST /auth/logout": return null;
    case "GET /me": return state.user;
    case "PATCH /me":
      if (typeof body.full_name === "string") state.user.full_name = body.full_name;
      if (typeof body.email === "string") state.user.email = body.email;
      return state.user;
    case "GET /api-keys": return state.keys;
    case "GET /api-keys/available-models": return modelNames;
    case "GET /models": return { ...modelCatalog, generated_at: new Date().toISOString() };
    case "POST /api-keys": {
      const id = crypto.randomUUID();
      const apiKey: ApiKeyRead = {
        id, user_id: demoUserId, name: String(body.name), environment: body.environment === "live" ? "live" : "test",
        prefix: "sk_demo", last_four: id.slice(-4), scopes: body.scopes as ApiKeyRead["scopes"],
        allowed_models: (body.allowed_models as string[] | null) ?? null,
        allowed_ips: (body.allowed_ips as string[] | null) ?? null,
        limit_period: (body.limit_period as ApiKeyRead["limit_period"]) ?? null,
        credit_limit: (body.credit_limit as number | null) ?? null,
        usd_limit_cents: (body.usd_limit_cents as number | null) ?? null,
        rate_limit_per_minute: Number(body.rate_limit_per_minute) || 120,
        created_at: new Date().toISOString(), expires_at: (body.expires_at as string | null) ?? null,
        last_used_at: null, revoked_at: null
      };
      state.keys.push(apiKey);
      return { ...apiKey, key: `sk_demo_not_a_real_key_${id}` };
    }
    case "GET /billing/plans": return state.plans.filter((plan) => plan.active && plan.visible);
    case "GET /billing/subscription": return state.subscription;
    case "POST /billing/subscription/cancel":
      if (body.cancel_at_period_end && !state.subscription.cancel_at_period_end) {
        recordBillingEvent("subscription_cancel_scheduled", `Cancellation scheduled for ${state.subscription.plan_name}`);
      }
      state.subscription.cancel_at_period_end = Boolean(body.cancel_at_period_end);
      return state.subscription;
    case "POST /billing/subscription/change": {
      const plan = required(state.plans.find((item) => item.id === body.plan_id));
      const interval: BillingInterval = body.interval === "year" ? "year" : "month";
      Object.assign(state.subscription, {
        plan_id: plan.id, plan_name: plan.name, interval,
        stripe_price_id: interval === "year" ? plan.stripe_annual_price_id : plan.stripe_price_id,
        amount_cents: interval === "year" ? plan.annual_price_cents : plan.price_cents,
        cancel_at_period_end: false
      });
      state.usage.stripe_allowance = plan.monthly_token_allowance;
      recalculateUsage();
      recordBillingEvent("subscription_updated", `Changed plan to ${plan.name}`, state.subscription.amount_cents);
      return state.subscription;
    }
    case "GET /billing/history": return paginate(state.billingHistory, url.searchParams);
    case "GET /billing/wallet": return { balance_tokens: 250_000 };
    case "GET /billing/wallet/bundles": return walletBundles;
    case "GET /referrals/me": return referralSummary();
    case "PUT /referrals/me/code": {
      // HYP-93: mirrors the backend's rules (app/referrals/service.py).
      const code = String(body.code ?? "").trim().toLowerCase();
      if (code.length < 3 || code.length > 32) throw new DemoError(400, "Referral code must be 3-32 characters.");
      if (!/^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/.test(code)) {
        throw new DemoError(400, "Referral code may only use letters, numbers, '-' and '_', and must start and end with a letter or number.");
      }
      if (["admin", "api", "signup", "login", "r", "referrals", "support", "talaris"].includes(code)) {
        throw new DemoError(400, "That referral code is reserved.");
      }
      if (demoTakenReferralCodes.includes(code)) throw new DemoError(409, "That referral code is already taken.");
      state.referralCode = code;
      return referralSummary();
    }
    case "GET /usage/current": return state.usage;
    case "POST /usage/recalculate": return recalculateUsage();
    case "GET /usage/allowance": return {
      period_start: state.usage.period_start, period_end: state.usage.period_end,
      stripe_monthly_allowance: state.usage.stripe_allowance, ethereum_erc20_balance_allowance: 0,
      substrate_evm_native_balance_allowance: 0, manual_adjustments: state.usage.manual_allowance,
      total_monthly_allowance: state.usage.total_allowance, used_tokens: state.usage.used_tokens,
      remaining_tokens: state.usage.remaining_tokens
    };
    case "GET /usage/history": return { data: paginate(state.events, url.searchParams).data };
    case "GET /usage/hourly": return usageHourly();
    case "GET /usage/by-model": return paginate(byModel(), url.searchParams);
    case "GET /wallets/evm": return state.wallet;
    case "DELETE /wallets/evm": state.wallet = null; return null;
    case "POST /wallets/evm/sync-balances":
      if (!state.wallet) throw new DemoError(404, "No demo wallet linked. Reload to reset the sample data.");
      state.wallet.last_balance_sync_at = new Date().toISOString();
      return { wallet: state.wallet, snapshots: [] };
    case "GET /admin/me": return {
      id: state.user.id, email: state.user.email, roles: [ownerRole], permissions: ownerRole.permissions
    };
    case "GET /admin/roles": return [ownerRole, supportRole];
    case "GET /admin/users": return paginate(adminUsers(), url.searchParams);
    case "GET /admin/api-keys": return paginate(state.keys, url.searchParams);
    case "GET /admin/billing/plans": return paginate(state.plans, url.searchParams);
    case "GET /admin/audit-logs": return paginate(state.auditLogs, url.searchParams);
    case "GET /admin/stats/overview": return {
      users_total: state.users.length, users_active: adminUsers().filter((user) => user.is_active).length,
      users_disabled: adminUsers().filter((user) => !user.is_active).length,
      active_api_keys: state.keys.filter((apiKey) => !apiKey.revoked_at).length,
      active_subscriptions: 1, visible_plans: state.plans.filter((plan) => plan.visible).length
    };
    case "GET /admin/stats/usage": return {
      total_used_tokens: state.usage.used_tokens, total_remaining_tokens: state.usage.remaining_tokens + 200_000,
      current_periods: state.users.length
    };
    case "GET /admin/stats/billing": return {
      billing_accounts: state.users.length, active_subscriptions: 1, trialing_subscriptions: 0, canceled_subscriptions: 0
    };
  }

  const modelMatch = route.match(/^\/usage\/by-model\/(.+)$/);
  if (method === "GET" && modelMatch) {
    const model = decodeURIComponent(modelMatch[1]);
    const summary = required(byModel().find((item) => item.model === model));
    const page = paginate(state.events.filter((event) => event.model === model), url.searchParams);
    return { summary, events: page.data, total_events: page.total, limit: page.limit, offset: page.offset };
  }

  const spendMatch = route.match(/^\/api-keys\/([^/]+)\/spend$/);
  if (method === "GET" && spendMatch) {
    const apiKey = required(state.keys.find((item) => item.id === spendMatch[1]));
    return {
      api_key_id: apiKey.id, limit_period: apiKey.limit_period, period_start: null, resets_at: null,
      credits_used: 0, usd_cents_used: 0, credit_limit: apiKey.credit_limit, usd_limit_cents: apiKey.usd_limit_cents
    };
  }

  const keyMatch = route.match(/^\/(?:admin\/)?api-keys\/([^/]+)(\/revoke)?$/);
  if (keyMatch && ["PATCH", "DELETE", "POST"].includes(method)) {
    const apiKey = required(state.keys.find((item) => item.id === keyMatch[1]));
    if (method === "PATCH") Object.assign(apiKey, body);
    else if (method === "DELETE" || keyMatch[2]) apiKey.revoked_at = new Date().toISOString();
    else throw new DemoError(501, "This API key action is unavailable in the local demo.");
    return method === "DELETE" ? null : apiKey;
  }

  const userMatch = route.match(/^\/admin\/users\/([^/]+)(.*)$/);
  if (userMatch) {
    const user = required(adminUsers().find((item) => item.id === userMatch[1]));
    const suffix = userMatch[2];
    if (method === "GET") {
      const isOwner = user.id === demoUserId;
      switch (suffix) {
        case "": return user;
        case "/api-keys": return paginate(state.keys.filter((item) => item.user_id === user.id), url.searchParams);
        case "/billing": return {
          ...state.subscription, user_id: user.id, stripe_customer_id: isOwner ? "cus_demo" : null,
          ...(isOwner ? {} : { status: "free", plan_id: state.plans[0].id, plan_name: "Free", stripe_subscription_id: null, stripe_price_id: null })
        };
        case "/usage": return {
          ...state.usage, user_id: user.id,
          ...(isOwner ? {} : { stripe_allowance: 100_000, manual_allowance: 0, total_allowance: 100_000, used_tokens: 0, remaining_tokens: 100_000 })
        };
        case "/wallets/evm": return {
          user_id: user.id, address: user.wallet_address,
          verified_at: user.wallet_address ? timestamp : null,
          last_balance_sync_at: user.wallet_address ? timestamp : null
        };
      }
    }
    if ((method === "PATCH" && !suffix) || (method === "POST" && ["/enable", "/disable"].includes(suffix))) {
      const update = suffix ? { is_active: suffix === "/enable" } : body;
      Object.assign(required(state.users.find((item) => item.id === user.id)), update);
      if (user.id === demoUserId) Object.assign(state.user, update);
      return required(adminUsers().find((item) => item.id === user.id));
    }
  }

  // No fallback to fetch: unsupported actions must never contact real services.
  throw new DemoError(501, "This action needs the backend and is unavailable in the local UI demo.");
}

export function demoRequest(path: string, method = "GET", body?: unknown): unknown {
  if (!UI_DEMO_MODE) throw new DemoError(403, "Local UI demo mode is disabled.");
  // Return response snapshots so a mutation cannot silently mutate React Query's cache.
  return structuredClone(handle(path, method, body));
}
