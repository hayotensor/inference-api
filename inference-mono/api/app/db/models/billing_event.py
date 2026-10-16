from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

# Kept as a plain string (not a DB enum) so a new kind can ship without a
# migration, matching WalletTransaction.kind.
BILLING_EVENT_TYPES = (
    "subscription_created",
    "subscription_updated",
    "subscription_renewed",
    "subscription_cancel_scheduled",
    "subscription_reactivated",
    "subscription_canceled",
    "subscription_payment",
    "payment_failed",
    "wallet_topup",
)


class BillingEvent(Base):
    """HYP-91: user-facing billing history. UserSubscription is a single row
    per user that each Stripe sync overwrites, so subscription creations,
    renewals, plan changes and cancellations leave no trail anywhere else --
    this table is that trail, appended by BillingService as Stripe state
    changes are synced (see app/billing/service.py `_record_billing_event`).
    Wallet top-ups are recorded here too (with their USD amount, which the
    WalletTransaction ledger does not carry)."""

    __tablename__ = "billing_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    plan_name: Mapped[str | None] = mapped_column(String(255))
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(16))
    tokens: Mapped[int | None] = mapped_column(BigInteger)
    # Stripe subscription / invoice / payment_intent id this event came from.
    # Invoice- and payment-intent-keyed events use it for idempotency against
    # a re-delivered webhook (on top of the StripeWebhookEvent dedupe).
    stripe_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
