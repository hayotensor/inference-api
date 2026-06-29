from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.service_client import ServiceClient


class InferenceUsageEvent(Base):
    __tablename__ = "inference_usage_events"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_inference_usage_events_user_request"),
        Index("ix_inference_usage_events_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    usage_period_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("usage_periods.id", ondelete="cascade"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="set null"), index=True
    )
    router_client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("service_clients.id", ondelete="set null"), index=True
    )
    miner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miners.id", ondelete="set null"), index=True
    )
    miner_hotkey: Mapped[str | None] = mapped_column(String(128), index=True)
    miner_model_hash: Mapped[str | None] = mapped_column(String(128))
    miner_model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miner_models.id", ondelete="set null"), index=True
    )
    miner_receipt_node_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1"), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    charged_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="reserved", index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    router_client: Mapped[ServiceClient | None] = relationship(
        "ServiceClient", back_populates="inference_usage_events"
    )
