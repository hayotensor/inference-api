import { authStore, tokenResponseToSession } from "@/lib/auth-store";
import { UI_DEMO_MODE } from "@/lib/demo-mode";
import type {
  ApiErrorDetail,
  AdminApiKeysResponse,
  AdminAssignment,
  AdminAuditLogsResponse,
  AdminBillingDetail,
  AdminBillingPlan,
  AdminBillingPlansResponse,
  AdminManualAdjustment,
  AdminMe,
  AdminRole,
  AdminStatsBilling,
  AdminStatsOverview,
  AdminStatsUsage,
  AdminUsageDetail,
  AdminUsersResponse,
  AdminUserDetail,
  AdminWallet,
  AdminWalletSyncResponse,
  ApiKeyCreateResponse,
  ApiKeyRead,
  ApiKeyScope,
  BalanceSyncResponse,
  BillingInterval,
  BillingPlan,
  BillingSubscription,
  EvmNonceResponse,
  EvmWallet,
  GuestInferenceResponse,
  MessageResponse,
  ReferralSummary,
  StripeSessionResponse,
  TokenResponse,
  UsageAllowance,
  UsageByModelDetailResponse,
  UsageByModelResponse,
  UsageHistoryResponse,
  UsagePeriod,
  User,
  WalletBalance,
  WalletTopupBundle
} from "@/lib/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
export const INFERENCE_API_BASE_URL =
  process.env.NEXT_PUBLIC_INFERENCE_API_BASE_URL ?? "http://localhost:8001";

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  auth?: boolean;
  apiKey?: string;
  retryOnUnauthorized?: boolean;
  headers?: HeadersInit;
  signal?: AbortSignal;
  baseUrl?: string;
};

type QueryValue = string | number | boolean | null | undefined;
type ApiCallOptions = Pick<RequestOptions, "signal">;

export class ApiError extends Error {
  status: number;
  code?: string;
  detail?: unknown;

  constructor(status: number, message: string, code?: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function trimBaseUrl(baseUrl: string) {
  return baseUrl.replace(/\/+$/, "");
}

function buildUrl(path: string, baseUrl = API_BASE_URL) {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${trimBaseUrl(baseUrl)}${path}`;
}

function withQuery(path: string, params: Record<string, QueryValue>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      query.set(key, String(value));
    }
  });
  const queryString = query.toString();
  return queryString ? `${path}?${queryString}` : path;
}

function extractErrorMessage(status: number, payload: unknown) {
  const fallback =
    status === 403
      ? "You do not have permission to perform this action."
      : status === 429
        ? "Too many requests. Please wait and try again."
        : "Request failed.";

  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: ApiErrorDetail }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "The submitted data is invalid.";
  if (detail && typeof detail === "object") {
    return detail.message ?? detail.detail ?? fallback;
  }
  return fallback;
}

function extractErrorCode(payload: unknown) {
  if (!payload || typeof payload !== "object") return undefined;
  const detail = (payload as { detail?: ApiErrorDetail }).detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    return detail.code;
  }
  return undefined;
}

let refreshPromise: Promise<boolean> | null = null;
const ACCESS_TOKEN_REFRESH_SKEW_MS = 60_000;

async function refreshAuthSession() {
  const refreshToken = authStore.refreshToken();
  if (!refreshToken) return false;

  if (!refreshPromise) {
    refreshPromise = fetch(buildUrl("/auth/refresh"), {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ refresh_token: refreshToken })
    })
      .then(async (response) => {
        if (!response.ok) {
          authStore.clear();
          return false;
        }
        const payload = (await response.json()) as TokenResponse;
        authStore.write(tokenResponseToSession(payload));
        return true;
      })
      .catch(() => {
        authStore.clear();
        return false;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }

  return refreshPromise;
}

async function accessTokenForAuthenticatedRequest() {
  const session = authStore.read();
  if (!session?.accessToken) {
    if (session?.refreshToken && (await refreshAuthSession())) {
      return authStore.accessToken();
    }
    return null;
  }

  if (session.expiresAt <= Date.now() + ACCESS_TOKEN_REFRESH_SKEW_MS) {
    if (await refreshAuthSession()) {
      return authStore.accessToken();
    }
    return null;
  }

  return session.accessToken;
}

export async function apiRequest<T>(
  path: string,
  {
    method = "GET",
    body,
    auth = false,
    apiKey,
    retryOnUnauthorized = true,
    headers,
    signal,
    baseUrl
  }: RequestOptions = {}
): Promise<T> {
  if (UI_DEMO_MODE) {
    signal?.throwIfAborted();
    if (auth && !authStore.accessToken()) {
      throw new ApiError(401, "Authentication required.", "unauthorized");
    }
    const { demoRequest, DemoError } = await import("@/lib/demo/api");
    signal?.throwIfAborted();
    try {
      return demoRequest(path, method, body) as T;
    } catch (error) {
      if (error instanceof DemoError) throw new ApiError(error.status, error.message, "demo");
      throw error;
    }
  }

  const requestHeaders = new Headers(headers);
  if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
  }
  if (auth) {
    const token = await accessTokenForAuthenticatedRequest();
    if (!token) {
      authStore.clear();
      throw new ApiError(401, "Authentication required.", "unauthorized");
    }
    requestHeaders.set("Authorization", `Bearer ${token}`);
  }
  if (apiKey) {
    requestHeaders.set("X-API-Key", apiKey);
  }

  const response = await fetch(buildUrl(path, baseUrl), {
    method,
    credentials: "include",
    headers: requestHeaders,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body)
  });

  if (response.status === 401 && auth && retryOnUnauthorized) {
    const refreshed = await refreshAuthSession();
    if (refreshed) {
      return apiRequest<T>(path, {
        method,
        body,
        auth,
        apiKey,
        retryOnUnauthorized: false,
        headers,
        signal
      });
    }
    authStore.clear();
  }

  if (response.status === 204) {
    return null as T;
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractErrorMessage(response.status, payload),
      extractErrorCode(payload),
      payload
    );
  }

  return payload as T;
}

export function apiErrorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/**
 * True for the "verify an email/phone/wallet before doing X" 403s the API
 * returns (e.g. creating an API key on an unverified account). Used to surface
 * a link to account verification instead of a generic permission-denied toast.
 */
export function isVerificationRequiredError(error: unknown) {
  return (
    error instanceof ApiError &&
    error.status === 403 &&
    /verify/i.test(error.message)
  );
}

/**
 * Set/refresh the httpOnly SSO cookie (via the same-origin route handler) so the
 * edge Worker can identify this user when routing to /chat. Best-effort.
 */
export async function syncSsoCookie() {
  if (UI_DEMO_MODE) return;
  if (typeof window === "undefined") return;
  const token = await accessTokenForAuthenticatedRequest();
  if (!token) return;
  await fetch("/api/sso-cookie", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }
  }).catch(() => {});
}

export async function clearSsoCookie() {
  if (UI_DEMO_MODE) return;
  if (typeof window === "undefined") return;
  await fetch("/api/sso-cookie", { method: "DELETE" }).catch(() => {});
}

export const authApi = {
  register(payload: {
    email: string;
    password: string;
    full_name?: string | null;
    // HYP-65: the `?ref=` code from a shared referral link, if the signup
    // page was opened with one. Omitted (not sent as null) when there is
    // none, since the backend's RegisterRequest schema treats "absent" and
    // "null" the same way -- either skips attribution.
    referral_code?: string;
  }) {
    return apiRequest<User>("/auth/register", { method: "POST", body: payload });
  },
  login(payload: { email: string; password: string }) {
    return apiRequest<TokenResponse>("/auth/login", {
      method: "POST",
      body: payload
    });
  },
  refresh(payload: { refresh_token: string }) {
    return apiRequest<TokenResponse>("/auth/refresh", {
      method: "POST",
      body: payload
    });
  },
  logout(refreshToken: string | null) {
    return apiRequest<void>("/auth/logout", {
      method: "POST",
      auth: true,
      body: { refresh_token: refreshToken }
    });
  },
  requestVerifyEmail(email: string) {
    return apiRequest<MessageResponse>("/auth/request-verify-email", {
      method: "POST",
      body: { email }
    });
  },
  verifyEmail(token: string) {
    return apiRequest<User>("/auth/verify-email", {
      method: "POST",
      body: { token }
    });
  },
  forgotPassword(email: string) {
    return apiRequest<MessageResponse>("/auth/forgot-password", {
      method: "POST",
      body: { email }
    });
  },
  resetPassword(payload: { token: string; password: string }) {
    return apiRequest<MessageResponse>("/auth/reset-password", {
      method: "POST",
      body: payload
    });
  },
  changePassword(payload: { current_password: string; new_password: string }) {
    return apiRequest<MessageResponse>("/auth/change-password", {
      method: "POST",
      auth: true,
      body: payload
    });
  },
  oauthLoginUrl(provider: "google" | "apple") {
    if (UI_DEMO_MODE) return "/login";
    return buildUrl(`/auth/${provider}/login`);
  },
  oauthCallback(provider: "google" | "apple", search: string) {
    return apiRequest<TokenResponse>(`/auth/${provider}/callback${search}`);
  },
  requestPhoneOtp(payload: {
    phone_number: string;
    purpose?: "login" | "link";
  }) {
    return apiRequest<MessageResponse & { expires_in: number }>(
      "/auth/phone/request-otp",
      {
        method: "POST",
        auth: payload.purpose === "link",
        body: { purpose: "login", ...payload }
      }
    );
  },
  verifyPhoneOtp(payload: {
    phone_number: string;
    code: string;
    email?: string;
    full_name?: string;
  }) {
    return apiRequest<TokenResponse>("/auth/phone/verify-otp", {
      method: "POST",
      body: payload
    });
  },
  linkPhone(payload: { phone_number: string; code: string }) {
    return apiRequest<User>("/auth/phone/link", {
      method: "POST",
      auth: true,
      body: payload
    });
  },
  unlinkPhone() {
    return apiRequest<User>("/auth/phone/unlink", {
      method: "POST",
      auth: true
    });
  },
  walletLoginNonce(address: string) {
    return apiRequest<{ address: string; message: string; expires_at: string }>(
      "/auth/wallet/nonce",
      { method: "POST", body: { address } }
    );
  },
  walletLoginVerify(payload: { address: string; message: string; signature: string }) {
    return apiRequest<TokenResponse>("/auth/wallet/verify", {
      method: "POST",
      body: payload
    });
  },
  ssoToken() {
    return apiRequest<{ sso_token: string; expires_in: number }>("/auth/sso/cookie", {
      method: "POST",
      auth: true
    });
  }
};

export const userApi = {
  me(options: ApiCallOptions = {}) {
    return apiRequest<User>("/me", { auth: true, signal: options.signal });
  },
  update(payload: { email?: string; password?: string; full_name?: string }) {
    return apiRequest<User>("/me", { method: "PATCH", auth: true, body: payload });
  },
  delete() {
    return apiRequest<void>("/me", { method: "DELETE", auth: true });
  }
};

export const apiKeysApi = {
  list(options: ApiCallOptions = {}) {
    return apiRequest<ApiKeyRead[]>("/api-keys", {
      auth: true,
      signal: options.signal
    });
  },
  // Known model ids a key could be scoped to (from currently-registered
  // miners). Powers the "restrict to models" picker below.
  availableModels(options: ApiCallOptions = {}) {
    return apiRequest<string[]>("/api-keys/available-models", {
      auth: true,
      signal: options.signal
    });
  },
  create(payload: {
    name: string;
    environment: "test" | "live";
    scopes: ApiKeyScope[];
    expires_at?: string | null;
    rate_limit_per_minute?: number | null;
    allowed_models?: string[] | null;
  }) {
    return apiRequest<ApiKeyCreateResponse>("/api-keys", {
      method: "POST",
      auth: true,
      body: payload
    });
  },
  update(
    keyId: string,
    payload: {
      name?: string;
      scopes?: ApiKeyScope[];
      expires_at?: string | null;
      rate_limit_per_minute?: number | null;
      allowed_models?: string[] | null;
    }
  ) {
    return apiRequest<ApiKeyRead>(`/api-keys/${keyId}`, {
      method: "PATCH",
      auth: true,
      body: payload
    });
  },
  revoke(keyId: string) {
    return apiRequest<void>(`/api-keys/${keyId}`, {
      method: "DELETE",
      auth: true
    });
  }
};

export const billingApi = {
  plans(options: ApiCallOptions = {}) {
    return apiRequest<BillingPlan[]>("/billing/plans", { signal: options.signal });
  },
  checkout(planId: string, interval: BillingInterval = "month") {
    return apiRequest<StripeSessionResponse>("/billing/stripe/checkout-session", {
      method: "POST",
      auth: true,
      body: { plan_id: planId, interval }
    });
  },
  // Confirms a just-completed Checkout Session directly with Stripe instead of
  // waiting on the webhook, so /billing/success can show the new plan right away.
  syncCheckoutSession(sessionId: string) {
    return apiRequest<BillingSubscription>("/billing/stripe/checkout-session/sync", {
      method: "POST",
      auth: true,
      body: { session_id: sessionId }
    });
  },
  portal() {
    return apiRequest<StripeSessionResponse>("/billing/stripe/customer-portal", {
      method: "POST",
      auth: true
    });
  },
  subscription(options: ApiCallOptions = {}) {
    return apiRequest<BillingSubscription>("/billing/subscription", {
      auth: true,
      signal: options.signal
    });
  },
  changePlan(planId: string, interval: BillingInterval = "month") {
    return apiRequest<BillingSubscription>("/billing/subscription/change", {
      method: "POST",
      auth: true,
      body: { plan_id: planId, interval }
    });
  },
  cancel(cancelAtPeriodEnd = true) {
    return apiRequest<BillingSubscription>("/billing/subscription/cancel", {
      method: "POST",
      auth: true,
      body: { cancel_at_period_end: cancelAtPeriodEnd }
    });
  },
  // HYP-66: pay-as-you-go wallet top-ups.
  walletBundles(options: ApiCallOptions = {}) {
    return apiRequest<WalletTopupBundle[]>("/billing/wallet/bundles", { signal: options.signal });
  },
  walletBalance(options: ApiCallOptions = {}) {
    return apiRequest<WalletBalance>("/billing/wallet", { auth: true, signal: options.signal });
  },
  walletCheckout(bundleId: string) {
    return apiRequest<StripeSessionResponse>("/billing/wallet/checkout-session", {
      method: "POST",
      auth: true,
      body: { bundle_id: bundleId }
    });
  }
};

// HYP-65: referral system.
export const referralsApi = {
  me(options: ApiCallOptions = {}) {
    return apiRequest<ReferralSummary>("/referrals/me", {
      auth: true,
      signal: options.signal
    });
  }
};

export const usageApi = {
  current(options: ApiCallOptions = {}) {
    return apiRequest<UsagePeriod>("/usage/current", {
      auth: true,
      signal: options.signal
    });
  },
  history(limit = 100, options: ApiCallOptions = {}) {
    return apiRequest<UsageHistoryResponse>(`/usage/history?limit=${limit}`, {
      auth: true,
      signal: options.signal
    });
  },
  allowance(options: ApiCallOptions = {}) {
    return apiRequest<UsageAllowance>("/usage/allowance", {
      auth: true,
      signal: options.signal
    });
  },
  byModel(params: { limit?: number; offset?: number } = {}, options: ApiCallOptions = {}) {
    const { limit = 50, offset = 0 } = params;
    return apiRequest<UsageByModelResponse>(
      `/usage/by-model?limit=${limit}&offset=${offset}`,
      { auth: true, signal: options.signal }
    );
  },
  byModelDetail(
    model: string,
    params: { limit?: number; offset?: number } = {},
    options: ApiCallOptions = {}
  ) {
    const { limit = 100, offset = 0 } = params;
    return apiRequest<UsageByModelDetailResponse>(
      `/usage/by-model/${encodeURIComponent(model)}?limit=${limit}&offset=${offset}`,
      { auth: true, signal: options.signal }
    );
  },
  recalculate(includeBalanceSync = false) {
    return apiRequest<UsagePeriod>("/usage/recalculate", {
      method: "POST",
      auth: true,
      body: { include_balance_sync: includeBalanceSync }
    });
  }
};

export const walletsApi = {
  nonce(address: string) {
    return apiRequest<EvmNonceResponse>("/wallets/evm/nonce", {
      method: "POST",
      auth: true,
      body: { address }
    });
  },
  verify(payload: { address: string; message: string; signature: string }) {
    return apiRequest<EvmWallet>("/wallets/evm/verify", {
      method: "POST",
      auth: true,
      body: payload
    });
  },
  get(options: ApiCallOptions = {}) {
    return apiRequest<EvmWallet | null>("/wallets/evm", {
      auth: true,
      signal: options.signal
    });
  },
  unlink() {
    return apiRequest<void>("/wallets/evm", { method: "DELETE", auth: true });
  },
  syncBalances() {
    return apiRequest<BalanceSyncResponse>("/wallets/evm/sync-balances", {
      method: "POST",
      auth: true
    });
  }
};

export const adminApi = {
  me(options: ApiCallOptions = {}) {
    return apiRequest<AdminMe>("/admin/me", {
      auth: true,
      signal: options.signal
    });
  },
  roles(options: ApiCallOptions = {}) {
    return apiRequest<AdminRole[]>("/admin/roles", {
      auth: true,
      signal: options.signal
    });
  },
  grantRole(userId: string, roleId: string) {
    return apiRequest<AdminAssignment>(`/admin/users/${userId}/roles`, {
      method: "POST",
      auth: true,
      body: { role_id: roleId }
    });
  },
  revokeRole(userId: string, roleId: string) {
    return apiRequest<AdminAssignment>(`/admin/users/${userId}/roles/${roleId}`, {
      method: "DELETE",
      auth: true
    });
  },
  users(params: {
    limit?: number;
    offset?: number;
    search?: string;
    status?: string;
    sort?: string;
  } = {}, options: ApiCallOptions = {}) {
    return apiRequest<AdminUsersResponse>(withQuery("/admin/users", params), {
      auth: true,
      signal: options.signal
    });
  },
  user(userId: string, options: ApiCallOptions = {}) {
    return apiRequest<AdminUserDetail>(`/admin/users/${userId}`, {
      auth: true,
      signal: options.signal
    });
  },
  updateUser(
    userId: string,
    payload: {
      email?: string;
      full_name?: string | null;
      phone_number?: string | null;
      is_active?: boolean;
      is_verified?: boolean;
    }
  ) {
    return apiRequest<AdminUserDetail>(`/admin/users/${userId}`, {
      method: "PATCH",
      auth: true,
      body: payload
    });
  },
  disableUser(userId: string) {
    return apiRequest<AdminUserDetail>(`/admin/users/${userId}/disable`, {
      method: "POST",
      auth: true
    });
  },
  enableUser(userId: string) {
    return apiRequest<AdminUserDetail>(`/admin/users/${userId}/enable`, {
      method: "POST",
      auth: true
    });
  },
  revokeUserSessions(userId: string) {
    return apiRequest<{ revoked_refresh_tokens: number }>(
      `/admin/users/${userId}/revoke-sessions`,
      { method: "POST", auth: true }
    );
  },
  apiKeys(params: {
    limit?: number;
    offset?: number;
    search?: string;
    status?: string;
    sort?: string;
  } = {}, options: ApiCallOptions = {}) {
    return apiRequest<AdminApiKeysResponse>(withQuery("/admin/api-keys", params), {
      auth: true,
      signal: options.signal
    });
  },
  userApiKeys(
    userId: string,
    params: {
      limit?: number;
      offset?: number;
      search?: string;
      status?: string;
      sort?: string;
    } = {},
    options: ApiCallOptions = {}
  ) {
    return apiRequest<AdminApiKeysResponse>(
      withQuery(`/admin/users/${userId}/api-keys`, params),
      { auth: true, signal: options.signal }
    );
  },
  updateApiKey(
    keyId: string,
    payload: {
      name?: string;
      scopes?: ApiKeyScope[];
      expires_at?: string | null;
      rate_limit_per_minute?: number | null;
      allowed_models?: string[] | null;
    }
  ) {
    return apiRequest<ApiKeyRead>(`/admin/api-keys/${keyId}`, {
      method: "PATCH",
      auth: true,
      body: payload
    });
  },
  revokeApiKey(keyId: string) {
    return apiRequest<ApiKeyRead>(`/admin/api-keys/${keyId}/revoke`, {
      method: "POST",
      auth: true
    });
  },
  billingPlans(params: {
    limit?: number;
    offset?: number;
    search?: string;
    status?: string;
    sort?: string;
  } = {}, options: ApiCallOptions = {}) {
    return apiRequest<AdminBillingPlansResponse>(
      withQuery("/admin/billing/plans", params),
      { auth: true, signal: options.signal }
    );
  },
  createBillingPlan(payload: {
    slug?: string | null;
    name: string;
    description?: string | null;
    stripe_price_id?: string | null;
    stripe_annual_price_id?: string | null;
    price_cents?: number;
    annual_price_cents?: number | null;
    currency?: string;
    monthly_token_allowance: number;
    active?: boolean;
    visible?: boolean;
    sort_order?: number;
    features?: Record<string, unknown>;
  }) {
    return apiRequest<AdminBillingPlan>("/admin/billing/plans", {
      method: "POST",
      auth: true,
      body: payload
    });
  },
  updateBillingPlan(
    planId: string,
    payload: Partial<{
      slug: string | null;
      name: string;
      description: string | null;
      stripe_price_id: string | null;
      stripe_annual_price_id: string | null;
      price_cents: number;
      annual_price_cents: number | null;
      currency: string;
      monthly_token_allowance: number;
      active: boolean;
      visible: boolean;
      sort_order: number;
      features: Record<string, unknown>;
    }>
  ) {
    return apiRequest<AdminBillingPlan>(`/admin/billing/plans/${planId}`, {
      method: "PATCH",
      auth: true,
      body: payload
    });
  },
  deactivateBillingPlan(planId: string) {
    return apiRequest<AdminBillingPlan>(
      `/admin/billing/plans/${planId}/deactivate`,
      { method: "POST", auth: true }
    );
  },
  activateBillingPlan(planId: string) {
    return apiRequest<AdminBillingPlan>(
      `/admin/billing/plans/${planId}/activate`,
      { method: "POST", auth: true }
    );
  },
  userBilling(userId: string, options: ApiCallOptions = {}) {
    return apiRequest<AdminBillingDetail>(`/admin/users/${userId}/billing`, {
      auth: true,
      signal: options.signal
    });
  },
  cancelUserSubscription(userId: string, cancelAtPeriodEnd = true) {
    return apiRequest<AdminBillingDetail>(
      `/admin/users/${userId}/billing/subscription/cancel`,
      {
        method: "POST",
        auth: true,
        body: { cancel_at_period_end: cancelAtPeriodEnd }
      }
    );
  },
  userUsage(userId: string, options: ApiCallOptions = {}) {
    return apiRequest<AdminUsageDetail>(`/admin/users/${userId}/usage`, {
      auth: true,
      signal: options.signal
    });
  },
  createManualAdjustment(userId: string, payload: { amount: number; reason: string }) {
    return apiRequest<AdminManualAdjustment>(
      `/admin/users/${userId}/usage/manual-adjustments`,
      {
        method: "POST",
        auth: true,
        body: payload
      }
    );
  },
  recalculateUserUsage(userId: string) {
    return apiRequest<AdminUsageDetail>(
      `/admin/users/${userId}/usage/recalculate`,
      { method: "POST", auth: true }
    );
  },
  userWallet(userId: string, options: ApiCallOptions = {}) {
    return apiRequest<AdminWallet>(`/admin/users/${userId}/wallets/evm`, {
      auth: true,
      signal: options.signal
    });
  },
  syncUserWalletBalances(userId: string) {
    return apiRequest<AdminWalletSyncResponse>(
      `/admin/users/${userId}/wallets/evm/sync-balances`,
      { method: "POST", auth: true }
    );
  },
  unlinkUserWallet(userId: string) {
    return apiRequest<AdminWallet>(`/admin/users/${userId}/wallets/evm/unlink`, {
      method: "POST",
      auth: true
    });
  },
  auditLogs(params: {
    limit?: number;
    offset?: number;
    search?: string;
    status?: string;
    created_from?: string;
    created_to?: string;
    sort?: string;
  } = {}, options: ApiCallOptions = {}) {
    return apiRequest<AdminAuditLogsResponse>(
      withQuery("/admin/audit-logs", params),
      { auth: true, signal: options.signal }
    );
  },
  statsOverview(options: ApiCallOptions = {}) {
    return apiRequest<AdminStatsOverview>("/admin/stats/overview", {
      auth: true,
      signal: options.signal
    });
  },
  statsUsage(options: ApiCallOptions = {}) {
    return apiRequest<AdminStatsUsage>("/admin/stats/usage", {
      auth: true,
      signal: options.signal
    });
  },
  statsBilling(options: ApiCallOptions = {}) {
    return apiRequest<AdminStatsBilling>("/admin/stats/billing", {
      auth: true,
      signal: options.signal
    });
  }
};

/**
 * The homepage's unauthenticated "try it now" sandbox (HYP-67). Goes through
 * this Next.js server's own `/api/guest-inference` route (NOT
 * `baseUrl: INFERENCE_API_BASE_URL` -- see that route's docstring for why:
 * the inference-api is not reachable directly from the browser in this
 * deployment), which forwards the real visitor IP onward. Identifies the
 * browser with a soft, client-generated id -- see `lib/guest-id.ts`. The
 * server independently caps this per-IP too; the client-side count this
 * returns is only ever the per-browser one.
 */
export const guestApi = {
  inference(prompt: string, guestId: string, options: ApiCallOptions = {}) {
    return apiRequest<GuestInferenceResponse>("/api/guest-inference", {
      method: "POST",
      // Relative to the current origin -- this is a same-origin Next.js route,
      // not the control-plane API that `apiRequest` targets by default.
      baseUrl: "",
      headers: { "X-Guest-Id": guestId },
      body: { prompt },
      signal: options.signal
    });
  }
};

export function redirectToStripeSession(response: StripeSessionResponse) {
  if (UI_DEMO_MODE) {
    throw new ApiError(501, "Stripe connections are disabled in the local UI demo.", "demo");
  }
  const url =
    response.url ??
    response.checkout_url ??
    response.session_url ??
    response.portal_url;
  if (!url) {
    throw new ApiError(502, "The API did not return a Stripe redirect URL.");
  }
  window.location.assign(url);
}
