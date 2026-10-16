from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import is_synthetic_phone_email, is_synthetic_wallet_email, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.api_key import APIKey
    from app.db.models.evm_wallet import EVMWallet
    from app.db.models.oauth_account import OAuthAccount
    from app.db.models.refresh_token import RefreshToken
    from app.db.models.usage_period import UsagePeriod
    from app.db.models.user_billing_account import UserBillingAccount
    from app.db.models.user_subscription import UserSubscription


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # HYP-65: this user's own shareable referral code. Generated lazily (see
    # app/referrals/service.py `ensure_referral_code`) on first access to
    # GET /referrals/me, not at signup -- an account that never opens the
    # Referrals page never needs one. Nullable until then. HYP-93: users can
    # replace it with a custom code (lowercase [a-z0-9_-], up to 32 chars);
    # uniqueness is case-insensitive via `ux_users_referral_code_lower` below.
    referral_code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wallet_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    billing_account: Mapped[UserBillingAccount | None] = relationship(
        "UserBillingAccount",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    subscription: Mapped[UserSubscription | None] = relationship(
        "UserSubscription",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    evm_wallet: Mapped[EVMWallet | None] = relationship(
        "EVMWallet",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    usage_periods: Mapped[list[UsagePeriod]] = relationship(
        "UsagePeriod", back_populates="user", cascade="all, delete-orphan"
    )

    id: Mapped[uuid.UUID]

    @property
    def oauth_providers(self) -> list[str]:
        """Names of OAuth providers linked to this account (e.g. ``["google"]``).

        Used to lock the ``email`` field in self-service profile updates: an
        OAuth-linked account's email is that provider's verified identity, not
        something the user should be able to silently change in Settings.
        """
        return [account.oauth_name for account in self.oauth_accounts]

    @property
    def has_verifiable_email(self) -> bool:
        """False when ``email`` is a synthetic placeholder with no real inbox.

        Phone-only and wallet-only accounts get a synthetic
        ``phone_<hash>@...``/``wallet_<hash>@...`` address (see
        ``synthetic_phone_email``/``synthetic_wallet_email`` in
        ``app.core.security``) so the ``users.email`` column can stay
        NOT NULL/unique without the account ever supplying a real one. There
        is nowhere for a "verify your email" message to go for those, so the
        UI uses this to hide that action instead of sending it into a void.
        """
        return not (is_synthetic_phone_email(self.email) or is_synthetic_wallet_email(self.email))


# HYP-93: case-insensitive uniqueness for referral codes. Generated codes are
# uppercase and custom ones lowercase, so the plain unique index above can't
# stop "abcd2345" from shadowing a generated "ABCD2345".
Index("ux_users_referral_code_lower", func.lower(User.referral_code), unique=True)
