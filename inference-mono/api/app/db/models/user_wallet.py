from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import utcnow
from app.db.base import Base


class UserWallet(Base):
    """Pay-as-you-go token balance (HYP-66). One row per user, topped up via a
    one-time Stripe Checkout Session and drawn down once the subscription
    allowance for the current usage period is exhausted. See
    app/usage/service.py for the spend-down order and WalletTransaction for the
    ledger of every credit/debit that moved this balance."""

    __tablename__ = "user_wallets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), primary_key=True
    )
    balance_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
