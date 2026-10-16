from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class Referral(Base):
    """HYP-65: attribution linking a referred signup to the user whose
    referral link (``{app_url}/r/<referral_code>``, or the older
    ``{app_url}/signup?ref=<referral_code>`` form) they signed up
    through. Recorded once, at signup time, and never changes afterward --
    `referred_user_id` is unique, so a user can be attributed to a referrer
    at most once, ever.

    Drives `ReferralEarning`/`WalletTransaction(kind="referral_bonus")`
    whenever `referred_user_id` pays Stripe (see app/referrals/service.py's
    `credit_for_payment`, called from app/billing/service.py), for as long as
    `created_at` is inside the 12-month earn-out window.
    """

    __tablename__ = "referrals"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    referrer_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    referred_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), unique=True, nullable=False
    )
    # The exact code that was used, kept even though it's derivable from
    # referrer_user_id.referral_code today -- a user's own code is never
    # reissued, but this keeps the historical record self-contained if that
    # ever changes.
    referral_code_used: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
