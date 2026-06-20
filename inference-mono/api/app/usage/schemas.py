from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class UsagePeriodRead(BaseModel):
    id: uuid.UUID
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

    model_config = {"from_attributes": True}


class UsageAllowanceRead(BaseModel):
    period_start: datetime
    period_end: datetime
    stripe_monthly_allowance: int
    ethereum_erc20_balance_allowance: int
    substrate_evm_native_balance_allowance: int
    manual_adjustments: int
    total_monthly_allowance: int
    used_tokens: int
    remaining_tokens: int


class UsageEventRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    api_key_id: uuid.UUID | None
    request_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    raw_total_tokens: int
    model_multiplier: Decimal
    charged_tokens: int
    status: str
    created_at: datetime
    settled_at: datetime | None = None

    model_config = {"from_attributes": True}


class UsageHistoryResponse(BaseModel):
    data: list[UsageEventRead]


class UsageRecalculateRequest(BaseModel):
    include_balance_sync: bool = Field(default=False)
