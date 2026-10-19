from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, DateTime, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.api_key_usage import APIKeyUsage
    from app.db.models.user import User


class APIKeyEnvironment(enum.StrEnum):
    live = "live"
    test = "test"


class APIKeyLimitPeriod(enum.StrEnum):
    daily = "daily"
    monthly = "monthly"
    never = "never"


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[APIKeyEnvironment] = mapped_column(
        Enum(APIKeyEnvironment, name="api_key_environment", native_enum=False),
        nullable=False,
        index=True,
    )
    prefix: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    hashed_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Optional model allowlist. NULL/empty = unrestricted (reaches every model);
    # non-empty = the key may only be used against these model ids.
    allowed_models: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Optional client IP allowlist (literal IPs and/or CIDRs). NULL/empty =
    # usable from anywhere. Enforced on the serving plane against the
    # trusted-proxy-resolved client IP (inference_api/client_ip.py).
    allowed_ips: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Optional spend caps (HYP-45). ``limit_period`` is one of
    # ``APIKeyLimitPeriod`` ("daily"/"monthly" UTC windows, or "never" for a
    # lifetime cap) and applies to both caps. ``credit_limit`` caps total
    # credits (charged tokens) this key consumes from any funding source;
    # ``usd_limit_cents`` caps the pay-as-you-go wallet spend (in USD cents).
    # NULL cap = no cap of that kind.
    limit_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    credit_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usd_limit_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[User] = relationship("User", back_populates="api_keys")
    usage_records: Mapped[list[APIKeyUsage]] = relationship(
        "APIKeyUsage", back_populates="api_key", cascade="all, delete-orphan"
    )
