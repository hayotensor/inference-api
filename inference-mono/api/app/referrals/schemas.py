from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


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
