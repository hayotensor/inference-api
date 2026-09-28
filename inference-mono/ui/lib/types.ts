export type UUID = string;

export type ApiErrorDetail =
  | string
  | {
      code?: string;
      message?: string;
      detail?: string;
      // Present on the guest-sandbox 429 (POST /v1/guest/inference) so the
      // homepage can show "X remaining" without a second round trip.
      remaining_today?: number;
    }
  | Array<unknown>;

export interface GuestInferenceResponse {
  id: string;
  model: string;
  output: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  attested: boolean;
  remaining_today: number;
}

export interface User {
  id: UUID;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  full_name: string | null;
  phone_number: string | null;
  phone_verified_at: string | null;
  created_at: string;
  oauth_providers: string[];
  has_verifiable_email: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
  auth_method?: "phone";
}

export interface MessageResponse {
  detail: string;
}

export interface AuthSession {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  user: User;
}

export type ApiKeyEnvironment = "test" | "live";
export type ApiKeyScope = "models:read" | "inference:write" | "usage:read";

export interface ApiKeyRead {
  id: UUID;
  user_id: UUID;
  name: string;
  environment: ApiKeyEnvironment;
  prefix: string;
  last_four: string;
  scopes: ApiKeyScope[];
  // null/empty = unrestricted (the key can reach every model).
  allowed_models: string[] | null;
  rate_limit_per_minute: number;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreateResponse extends ApiKeyRead {
  key: string;
}

export type BillingInterval = "month" | "year";

export interface BillingPlan {
  id: UUID;
  slug: string;
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
}

export interface StripeSessionResponse {
  id?: string;
  url?: string;
  checkout_url?: string;
  session_url?: string;
  portal_url?: string;
}

export interface BillingSubscription {
  id: UUID | null;
  stripe_subscription_id: string | null;
  stripe_price_id: string | null;
  plan_id: UUID | null;
  plan_name: string | null;
  interval: BillingInterval | null;
  amount_cents: number | null;
  currency: string | null;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

export interface UsagePeriod {
  id: UUID;
  user_id: UUID;
  period_start: string;
  period_end: string;
  stripe_allowance: number;
  ethereum_erc20_allowance: number;
  substrate_native_allowance: number;
  manual_allowance: number;
  total_allowance: number;
  used_tokens: number;
  remaining_tokens: number;
  calculated_at: string;
}

export interface UsageAllowance {
  period_start: string;
  period_end: string;
  stripe_monthly_allowance: number;
  ethereum_erc20_balance_allowance: number;
  substrate_evm_native_balance_allowance: number;
  manual_adjustments: number;
  total_monthly_allowance: number;
  used_tokens: number;
  remaining_tokens: number;
}

export interface UsageEvent {
  id: UUID;
  user_id: UUID;
  api_key_id: UUID | null;
  request_id: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  raw_total_tokens: number;
  model_multiplier: string;
  charged_tokens: number;
  status: string;
  created_at: string;
  settled_at: string | null;
}

export interface UsageHistoryResponse {
  data: UsageEvent[];
}

export interface UsageByModel {
  model: string;
  request_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  charged_tokens: number;
  last_used_at: string | null;
}

export interface UsageByModelResponse {
  data: UsageByModel[];
  total: number;
  limit: number;
  offset: number;
}

export interface UsageByModelDetailResponse {
  summary: UsageByModel;
  events: UsageEvent[];
  total_events: number;
  limit: number;
  offset: number;
}

export interface EvmNonceResponse {
  address: string;
  nonce: string;
  message: string;
  expires_at: string;
}

export interface EvmWallet {
  address: string;
  verified_at: string;
  last_balance_sync_at: string | null;
}

export interface BalanceSyncItem {
  chain: string;
  token_type: string;
  token_contract_address: string | null;
  raw_balance: string;
  normalized_balance: string;
  inference_token_allowance: number;
  block_number: number | null;
  error_message: string | null;
  checked_at: string;
}

export interface BalanceSyncResponse {
  wallet: EvmWallet;
  snapshots: BalanceSyncItem[];
}

export type AdminPermission =
  | "admins.read"
  | "admins.write"
  | "admins.owner.write"
  | "users.read"
  | "users.write"
  | "users.disable"
  | "users.sessions.revoke"
  | "users.sensitive.read"
  | "api_keys.read"
  | "api_keys.write"
  | "api_keys.revoke"
  | "billing.read"
  | "billing.sensitive.read"
  | "billing.plans.read"
  | "billing.plans.write"
  | "billing.subscriptions.write"
  | "usage.read"
  | "usage.write"
  | "wallets.read"
  | "wallets.write"
  | "wallets.sensitive.read"
  | "audit.read"
  | "audit.sensitive.read"
  | "stats.read";

export interface AdminRole {
  id: UUID;
  slug: string;
  name: string;
  description: string | null;
  system: boolean;
  permissions: AdminPermission[];
}

export interface AdminAssignment {
  id: UUID;
  user_id: UUID;
  role: AdminRole;
  granted_by_user_id: UUID | null;
  granted_at: string;
  revoked_at: string | null;
}

export interface AdminMe {
  id: UUID;
  email: string;
  roles: AdminRole[];
  permissions: AdminPermission[];
}

export interface AdminUserSummary {
  id: UUID;
  email: string;
  full_name: string | null;
  phone_number: string | null;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  api_key_count: number;
  active_api_key_count: number;
}

export interface AdminUsersResponse {
  data: AdminUserSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminUserDetail extends AdminUserSummary {
  phone_verified_at: string | null;
  updated_at: string;
  roles: AdminRole[];
  oauth_providers: string[];
  billing_status: string | null;
  wallet_address: string | null;
  current_period_remaining_tokens: number | null;
}

export interface AdminApiKeysResponse {
  data: ApiKeyRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminBillingPlan extends BillingPlan {
  created_at: string;
  updated_at: string;
}

export interface AdminBillingPlansResponse {
  data: AdminBillingPlan[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminBillingDetail {
  user_id: UUID;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  stripe_price_id: string | null;
  plan_id: UUID | null;
  plan_name: string | null;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

export interface AdminUsageDetail {
  user_id: UUID;
  period_start: string;
  period_end: string;
  stripe_allowance: number;
  ethereum_erc20_allowance: number;
  substrate_native_allowance: number;
  manual_allowance: number;
  total_allowance: number;
  used_tokens: number;
  remaining_tokens: number;
  calculated_at: string;
}

export interface AdminManualAdjustment {
  id: UUID;
  user_id: UUID;
  amount: number;
  reason: string;
  admin_actor_id: UUID | null;
  created_at: string;
}

export interface AdminWallet {
  user_id: UUID;
  address: string | null;
  verified_at: string | null;
  last_balance_sync_at: string | null;
}

export interface AdminWalletSyncResponse {
  wallet: AdminWallet;
  snapshots_created: number;
}

export interface AdminAuditLog {
  id: UUID;
  user_id: UUID | null;
  actor_user_id: UUID | null;
  action: string;
  actor: string | null;
  target_type: string | null;
  target_id: string | null;
  result: string;
  request_id: string | null;
  ip_address: string | null;
  user_agent: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AdminAuditLogsResponse {
  data: AdminAuditLog[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminStatsOverview {
  users_total: number;
  users_active: number;
  users_disabled: number;
  active_api_keys: number;
  active_subscriptions: number;
  visible_plans: number;
}

export interface AdminStatsUsage {
  total_used_tokens: number;
  total_remaining_tokens: number;
  current_periods: number;
}

export interface AdminStatsBilling {
  billing_accounts: number;
  active_subscriptions: number;
  trialing_subscriptions: number;
  canceled_subscriptions: number;
}

// One approved model artifact from the platform-signed allowlist. Mirrors
// talaris_contracts.allowlist.AllowlistEntry on the wire.
export interface AttestedModelEntry {
  model_id: string;
  model_version?: string | null;
  model_hash: string;
  args_hash?: string | null;
  gpu_hash?: string | null;
  label?: string | null;
}

// HYP-37: the frontend's own independent re-verification result of the
// inference-api's signed model allowlist. See
// app/api/attestation-status/route.ts (producer) and
// components/attestation-badge.tsx (consumer).
export interface AttestationStatusResponse {
  status: "verified" | "unverified";
  checkedAt: string;
  reason?: string;
  platformPubkey?: string;
  allowlistVersion?: number;
  entryCount?: number;
  // Only populated when status is "verified" -- the actual attested claims,
  // not just a count. Absent on any unverified result so nothing unverified
  // is ever rendered as if it were checked.
  entries?: AttestedModelEntry[];
}

// HYP-58: live per-model service status, proxied from the inference-api's
// public `GET /status/models` (see app/api/status/route.ts and
// inference_api/status_routes.py, the producer). A snapshot of current miner
// availability -- not a historical uptime log.
export type ModelServiceStatus = "operational" | "degraded" | "down";

export interface ModelStatusEntry {
  model_id: string;
  label: string | null;
  status: ModelServiceStatus;
  healthy_miner_count: number;
  registered_miner_count: number;
}

export interface StatusResponse {
  generated_at: string;
  overall_status: ModelServiceStatus;
  models: ModelStatusEntry[];
}
