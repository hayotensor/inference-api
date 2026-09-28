from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

# Kept as a plain string (not a DB enum) so a new kind can ship without a
# migration, matching how app/db/models/manual_token_adjustment.py treats
# `reason` as free text rather than a constrained column.
WALLET_TRANSACTION_KINDS = ("purchase", "debit", "refund", "adjustment", "referral_bonus")


class WalletTransaction(Base):
    """Ledger of every change to a UserWallet.balance_tokens (HYP-66): Stripe
    top-up purchases (kind="purchase"), inference spend once the subscription
    allowance runs out (kind="debit", delta_tokens negative), manual
    corrections (kind="refund"/"adjustment"), and HYP-65 referral bonuses
    (kind="referral_bonus", credited to the referrer -- see
    app/referrals/service.py and ReferralEarning for the matching ledger of
    *why* each one happened)."""

    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    # Positive = credit (purchase/refund/positive adjustment), negative = debit
    # (inference spend/negative adjustment).
    delta_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Set only for kind="purchase" rows, and used as the idempotency key that
    # keeps a re-delivered Stripe webhook from crediting the wallet twice (in
    # addition to the StripeWebhookEvent dedupe on the event id itself).
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
