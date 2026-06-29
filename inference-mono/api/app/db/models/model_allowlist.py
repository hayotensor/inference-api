from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class ModelAllowlist(Base):
    """Platform-approved model artifacts: the canonical (model_id -> approved hash) set the
    attestation verifier checks a miner's attested model_hash against. Mirrors
    ``talaris_attest.claims.ModelAllowlistEntry``."""

    __tablename__ = "model_allowlist"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "model_version", "model_hash", name="uq_model_allowlist_model_version_hash"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    model_id: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(120))
    model_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    args_hash: Mapped[str | None] = mapped_column(String(128))
    gpu_hash: Mapped[str | None] = mapped_column(String(128))
    label: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
