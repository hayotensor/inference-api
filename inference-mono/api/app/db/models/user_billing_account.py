from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.user import User


class UserBillingAccount(Base):
    __tablename__ = "user_billing_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), unique=True, index=True
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True
    )
    # Set when a Checkout Session is created and cleared once it resolves (completed
    # or expired). Lets create_checkout_session refuse a second session for this
    # account while one is still open, closing the window where a double-click or a
    # retry — before the webhook has synced the first payment — would let Stripe bill
    # the customer twice.
    pending_checkout_session_id: Mapped[str | None] = mapped_column(String(255))
    pending_checkout_session_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="billing_account")
