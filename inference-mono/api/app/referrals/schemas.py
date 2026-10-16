from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReferralEarningRead(BaseModel):
    id: uuid.UUID
    tokens_credited: int
    usd_equivalent_cents: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ReferralSummaryRead(BaseModel):
    referral_code: str
    referral_link: str
    total_referrals: int
    total_tokens_earned: int
    recent_earnings: list[ReferralEarningRead]

    model_config = {"from_attributes": True}


class ReferralCodeUpdate(BaseModel):
    """HYP-93: a user-chosen referral code. Loosely bounded here; the real
    rules (3-32 chars of [a-z0-9_-], case-insensitive, not reserved) are
    enforced in ReferralService so each failure gets its own error code."""

    code: str = Field(min_length=1, max_length=64)
