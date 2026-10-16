from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class ReferralCodeAlias(Base):
    """HYP-93: a referral code a user *used to* have before changing it to a
    custom one. Kept so every link already shared with the old code
    (``/r/<old>`` or ``/signup?ref=<old>``) keeps attributing signups to the
    same user, and so nobody else can claim that code later. Lookups and
    uniqueness are case-insensitive (functional unique index on
    ``lower(code)``), matching ``users.referral_code``.
    """

    __tablename__ = "referral_code_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


Index(
    "ux_referral_code_aliases_code_lower",
    func.lower(ReferralCodeAlias.code),
    unique=True,
)
