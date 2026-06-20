from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.admin_role import AdminRole
    from app.db.models.user import User


class AdminAssignment(Base):
    __tablename__ = "admin_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_roles.id", ondelete="cascade"), index=True
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="set null"), index=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    role: Mapped[AdminRole] = relationship("AdminRole", back_populates="assignments", lazy="selectin")
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    granted_by: Mapped[User | None] = relationship("User", foreign_keys=[granted_by_user_id])
