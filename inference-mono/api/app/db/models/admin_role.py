from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.admin_assignment import AdminAssignment
    from app.db.models.admin_role_permission import AdminRolePermission


class AdminRole(Base):
    __tablename__ = "admin_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    permissions: Mapped[list[AdminRolePermission]] = relationship(
        "AdminRolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    assignments: Mapped[list[AdminAssignment]] = relationship(
        "AdminAssignment",
        back_populates="role",
        cascade="all, delete-orphan",
    )
