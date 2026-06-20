from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.user import User


class UsagePeriod(Base):
    __tablename__ = "usage_periods"
    __table_args__ = (
        UniqueConstraint("user_id", "period_start", "period_end", name="uq_usage_periods_user_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    stripe_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ethereum_erc20_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    substrate_native_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    manual_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    remaining_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="usage_periods")
