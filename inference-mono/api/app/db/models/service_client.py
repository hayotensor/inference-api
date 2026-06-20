from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.inference_usage_event import InferenceUsageEvent


class ServiceClientRole(enum.StrEnum):
    router = "router"


class ServiceClient(Base):
    __tablename__ = "service_clients"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[ServiceClientRole] = mapped_column(
        Enum(ServiceClientRole, name="service_client_role", native_enum=False),
        default=ServiceClientRole.router,
        index=True,
        nullable=False,
    )
    prefix: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    hashed_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    inference_usage_events: Mapped[list[InferenceUsageEvent]] = relationship(
        "InferenceUsageEvent", back_populates="router_client"
    )
