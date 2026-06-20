from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.admin_role import AdminRole


class AdminRolePermission(Base):
    __tablename__ = "admin_role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uq_admin_role_permissions_role_permission"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("admin_roles.id", ondelete="cascade"), index=True
    )
    permission: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    role: Mapped[AdminRole] = relationship("AdminRole", back_populates="permissions")
