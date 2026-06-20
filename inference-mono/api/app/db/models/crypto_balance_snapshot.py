from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class CryptoBalanceSnapshot(Base):
    __tablename__ = "crypto_balance_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    wallet_address: Mapped[str] = mapped_column(String(42), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    token_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    token_contract_address: Mapped[str | None] = mapped_column(String(42), index=True)
    raw_balance: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_balance: Mapped[Decimal] = mapped_column(Numeric(48, 18), nullable=False)
    inference_token_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(String(512))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
