from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class ReferralEarning(Base):
    """HYP-65: one row per Stripe payment that earned the referrer a wallet
    credit -- 10% of the referred user's paid amount, converted to bonus
    inference-token credits at that payment's own effective $/token rate (see
    app/referrals/service.py `credit_for_payment`). Paid in tokens, never
    cash -- see the HYP-65 report for that scope cut.

    Also produces a matching `WalletTransaction(kind="referral_bonus", ...)`
    on the referrer's wallet (so it shows up in the existing HYP-66 wallet
    ledger/UI); this table is the audit trail of *why* that credit happened.
    """

    __tablename__ = "referral_earnings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    referral_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("referrals.id", ondelete="cascade"), index=True, nullable=False
    )
    # The Stripe invoice id (subscription payments, invoice.paid) or
    # payment_intent id (HYP-66 wallet top-ups, which never produce an
    # invoice) that earned this credit. Unique so a retried/duplicate webhook
    # delivery can never credit the same payment twice -- the same
    # belt-and-suspenders idempotency pattern HYP-66 uses for wallet top-up
    # purchases (see WalletTransaction.stripe_payment_intent_id), on top of
    # (not instead of) the StripeWebhookEvent dedupe in process_webhook.
    source_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    referred_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), nullable=False
    )
    referrer_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    tokens_credited: Mapped[int] = mapped_column(BigInteger, nullable=False)
    usd_equivalent_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
