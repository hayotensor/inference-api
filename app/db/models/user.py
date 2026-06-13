from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.api_key import APIKey
    from app.db.models.oauth_account import OAuthAccount
    from app.db.models.refresh_token import RefreshToken


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount", lazy="selectin", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list[APIKey]] = relationship(
        "APIKey", back_populates="user", cascade="all, delete-orphan"
    )

    id: Mapped[uuid.UUID]
