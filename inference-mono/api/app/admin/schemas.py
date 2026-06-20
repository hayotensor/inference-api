from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.db.models.api_key import APIKeyEnvironment
from app.db.models.service_client import ServiceClientRole


class AdminRolePermissionRead(BaseModel):
    permission: str


class AdminRoleRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    system: bool
    permissions: list[str]


class AdminRoleAssignmentCreate(BaseModel):
    role_id: uuid.UUID


class AdminAssignmentRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role: AdminRoleRead
    granted_by_user_id: uuid.UUID | None = None
    granted_at: datetime
    revoked_at: datetime | None = None


class AdminMeRead(BaseModel):
    id: uuid.UUID
    email: str
    roles: list[AdminRoleRead]
    permissions: list[str]


class AdminUserSummaryRead(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    phone_number: str | None = None
    is_active: bool
    is_verified: bool
    created_at: datetime
    api_key_count: int
    active_api_key_count: int


class AdminUsersResponse(BaseModel):
    data: list[AdminUserSummaryRead]
    total: int
    limit: int
    offset: int


class AdminUserDetailRead(AdminUserSummaryRead):
    phone_verified_at: datetime | None = None
    updated_at: datetime
    roles: list[AdminRoleRead]
    oauth_providers: list[str]
    billing_status: str | None = None
    wallet_address: str | None = None
    current_period_remaining_tokens: int | None = None


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=255)
    phone_number: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None
    is_verified: bool | None = None


class AdminAPIKeyRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    environment: APIKeyEnvironment
    prefix: str
    last_four: str
    scopes: list[str]
    rate_limit_per_minute: int
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class AdminAPIKeysResponse(BaseModel):
    data: list[AdminAPIKeyRead]
    total: int
    limit: int
    offset: int


class AdminAPIKeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)


class AdminServiceClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: ServiceClientRole = ServiceClientRole.router
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)


class AdminServiceClientRead(BaseModel):
    id: uuid.UUID
    name: str
    role: ServiceClientRole
    prefix: str
    last_four: str
    rate_limit_per_minute: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class AdminServiceClientCreateResponse(AdminServiceClientRead):
    token: str


class AdminServiceClientsResponse(BaseModel):
    data: list[AdminServiceClientRead]
    total: int
    limit: int
    offset: int


class AdminBillingPlanRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    stripe_price_id: str | None = None
    monthly_token_allowance: int
    active: bool
    visible: bool
    sort_order: int
    features: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminBillingPlansResponse(BaseModel):
    data: list[AdminBillingPlanRead]
    total: int
    limit: int
    offset: int


class AdminBillingPlanCreate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=512)
    stripe_price_id: str | None = Field(default=None, max_length=255)
    monthly_token_allowance: int = Field(ge=0)
    active: bool = True
    visible: bool = True
    sort_order: int = 0
    features: dict[str, Any] = Field(default_factory=dict)

    @field_validator("stripe_price_id")
    @classmethod
    def blank_stripe_price_id_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminBillingPlanUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=512)
    stripe_price_id: str | None = Field(default=None, max_length=255)
    monthly_token_allowance: int | None = Field(default=None, ge=0)
    active: bool | None = None
    visible: bool | None = None
    sort_order: int | None = None
    features: dict[str, Any] | None = None

    @field_validator("stripe_price_id")
    @classmethod
    def blank_stripe_price_id_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminBillingDetailRead(BaseModel):
    user_id: uuid.UUID
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    stripe_price_id: str | None = None
    plan_id: uuid.UUID | None = None
    plan_name: str | None = None
    status: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False


class AdminUsageDetailRead(BaseModel):
    user_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    stripe_allowance: int
    ethereum_erc20_allowance: int
    substrate_native_allowance: int
    manual_allowance: int
    total_allowance: int
    used_tokens: int
    remaining_tokens: int
    calculated_at: datetime


class AdminManualAdjustmentCreate(BaseModel):
    amount: int
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("amount")
    @classmethod
    def amount_cannot_be_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("amount cannot be zero")
        return value


class AdminManualAdjustmentRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    amount: int
    reason: str
    admin_actor_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminWalletRead(BaseModel):
    user_id: uuid.UUID
    address: str | None = None
    verified_at: datetime | None = None
    last_balance_sync_at: datetime | None = None


class AdminWalletSyncResponse(BaseModel):
    wallet: AdminWalletRead
    snapshots_created: int


class AdminAuditLogRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    action: str
    actor: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    result: str
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    metadata: dict[str, Any]
    created_at: datetime


class AdminAuditLogsResponse(BaseModel):
    data: list[AdminAuditLogRead]
    total: int
    limit: int
    offset: int


class AdminStatsOverviewRead(BaseModel):
    users_total: int
    users_active: int
    users_disabled: int
    active_api_keys: int
    active_subscriptions: int
    visible_plans: int


class AdminStatsUsageRead(BaseModel):
    total_used_tokens: int
    total_remaining_tokens: int
    current_periods: int


class AdminStatsBillingRead(BaseModel):
    billing_accounts: int
    active_subscriptions: int
    trialing_subscriptions: int
    canceled_subscriptions: int
