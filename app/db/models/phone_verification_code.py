import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class PhoneVerificationPurpose(enum.StrEnum):
    login = "login"
    link = "link"


class PhoneVerificationCode(Base):
    __tablename__ = "phone_verification_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    purpose: Mapped[PhoneVerificationPurpose] = mapped_column(
        Enum(PhoneVerificationPurpose, name="phone_verification_purpose", native_enum=False),
        index=True,
        nullable=False,
    )
    otp_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    request_ip: Mapped[str | None] = mapped_column(String(64))
