from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

BillingInterval = Literal["month", "year"]


class BillingPlanRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    stripe_price_id: str | None
    stripe_annual_price_id: str | None = None
    price_cents: int = 0
    annual_price_cents: int | None = None
    currency: str = "usd"
    monthly_token_allowance: int
    active: bool
    visible: bool
    sort_order: int
    features: dict[str, Any]

    model_config = {"from_attributes": True}


class StripeCheckoutSessionRequest(BaseModel):
    plan_id: uuid.UUID
    interval: BillingInterval = "month"


class StripeSessionResponse(BaseModel):
    id: str
    url: str


class SyncCheckoutSessionRequest(BaseModel):
    session_id: str


class BillingSubscriptionRead(BaseModel):
    id: uuid.UUID | None = None
    stripe_subscription_id: str | None = None
    stripe_price_id: str | None = None
    plan_id: uuid.UUID | None = None
    plan_name: str | None = None
    interval: BillingInterval | None = None
    amount_cents: int | None = None
    currency: str | None = None
    status: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False


class ChangeSubscriptionRequest(BaseModel):
    plan_id: uuid.UUID
    interval: BillingInterval = "month"


class CancelSubscriptionRequest(BaseModel):
    cancel_at_period_end: bool = True


class StripeWebhookResponse(BaseModel):
    received: bool
    processed: bool
